// data-qa-agent :: main infra (resource-group scoped, env-parameterized).
// Deploy `dev` now; `staging`/`prod` are the same template with a different `env`.
//
// Two-phase deploy (see .github/workflows/deploy.yml):
//   1) deployApps=false  -> platform (ACR, identity, KV, Postgres, secrets, CA env)
//      ...build & push images to the ACR, using the apiUrl output for the frontend...
//   2) deployApps=true   -> the container apps + migration job
//
targetScope = 'resourceGroup'

@description('Logical environment: dev | staging | prod')
param env string = 'dev'
param namePrefix string = 'dataqa'
param location string = resourceGroup().location

@description('Container image tag to deploy (e.g. a git SHA).')
param imageTag string = 'latest'
param useClaude bool = false

@description('Phase 1 = false (platform only); phase 2 = true (deploy the apps).')
param deployApps bool = true

@secure()
param postgresAdminPassword string
@secure()
param appUserPassword string
@secure()
param agentRoPassword string
@secure()
param jwtSecret string
@secure()
param anthropicApiKey string = ''

var tags = {
  project: 'data-qa-agent'
  env: env
  managedBy: 'bicep'
}

module monitoring 'modules/monitoring.bicep' = {
  name: 'monitoring'
  params: { namePrefix: namePrefix, env: env, location: location, tags: tags }
}

module registry 'modules/registry.bicep' = {
  name: 'registry'
  params: { namePrefix: namePrefix, env: env, location: location, tags: tags }
}

module identity 'modules/identity.bicep' = {
  name: 'identity'
  params: {
    namePrefix: namePrefix
    env: env
    location: location
    tags: tags
    acrId: registry.outputs.id
  }
}

module keyvault 'modules/keyvault.bicep' = {
  name: 'keyvault'
  params: {
    namePrefix: namePrefix
    env: env
    location: location
    tags: tags
    principalId: identity.outputs.principalId
    postgresAdminPassword: postgresAdminPassword
    anthropicApiKey: anthropicApiKey
  }
}

module postgres 'modules/postgres.bicep' = {
  name: 'postgres'
  params: {
    namePrefix: namePrefix
    env: env
    location: location
    tags: tags
    administratorPassword: postgresAdminPassword
  }
}

module secrets 'modules/secrets.bicep' = {
  name: 'secrets'
  params: {
    kvName: keyvault.outputs.name
    pgFqdn: postgres.outputs.fqdn
    databaseName: postgres.outputs.databaseName
    adminLogin: postgres.outputs.administratorLogin
    adminPassword: postgresAdminPassword
    appUserPassword: appUserPassword
    agentRoPassword: agentRoPassword
    jwtSecret: jwtSecret
  }
}

module environment 'modules/environment.bicep' = {
  name: 'environment'
  params: {
    namePrefix: namePrefix
    env: env
    location: location
    tags: tags
    logWorkspaceCustomerId: monitoring.outputs.customerId
    logWorkspaceSharedKey: monitoring.outputs.primarySharedKey
  }
}

var acrServer = registry.outputs.loginServer

module apps 'modules/containerapps.bicep' = if (deployApps) {
  name: 'containerapps'
  dependsOn: [secrets]
  params: {
    namePrefix: namePrefix
    env: env
    location: location
    tags: tags
    caeId: environment.outputs.id
    caeDefaultDomain: environment.outputs.defaultDomain
    acrLoginServer: acrServer
    managedIdentityId: identity.outputs.id
    keyVaultUri: keyvault.outputs.vaultUri
    frontendImage: '${acrServer}/frontend:${imageTag}'
    backendImage: '${acrServer}/backend-api:${imageTag}'
    agentImage: '${acrServer}/data-agent:${imageTag}'
    jobImage: '${acrServer}/db-migrate:${imageTag}'
    useClaude: useClaude
  }
}

// URLs are derived from the CA environment domain, so they're known in phase 1
// (before the apps exist) — the frontend build needs apiUrl.
output acrLoginServer string = acrServer
output acrName string = registry.outputs.name
output keyVaultName string = keyvault.outputs.name
output apiUrl string = 'https://${namePrefix}-${env}-api.${environment.outputs.defaultDomain}'
output frontendUrl string = 'https://${namePrefix}-${env}-web.${environment.outputs.defaultDomain}'
