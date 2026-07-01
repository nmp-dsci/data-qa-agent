// Container Apps environment + the three services and the migration job.
// Ingress: frontend (public), backend-api (public — the browser calls it),
// data-agent (internal only). FQDNs are derived from the env's default domain
// so apps can reference each other without ordering dependencies.
param namePrefix string
param env string
param location string
param tags object

param caeId string
param caeDefaultDomain string

param acrLoginServer string
param managedIdentityId string
param keyVaultUri string

param frontendImage string
param backendImage string
param agentImage string
param jobImage string

param useClaude bool = false

var domain = caeDefaultDomain
var frontendFqdn = '${namePrefix}-${env}-web.${domain}'
var apiFqdn = '${namePrefix}-${env}-api.${domain}'
var agentInternalFqdn = '${namePrefix}-${env}-agent.internal.${domain}'

var identityConfig = {
  type: 'UserAssigned'
  userAssignedIdentities: { '${managedIdentityId}': {} }
}
var registries = [
  { server: acrLoginServer, identity: managedIdentityId }
]

// ---- data-agent (internal only) -------------------------------------------
var agentSecrets = concat(
  [{ name: 'database-url', keyVaultUrl: '${keyVaultUri}secrets/agent-database-url', identity: managedIdentityId }],
  useClaude ? [{ name: 'anthropic-api-key', keyVaultUrl: '${keyVaultUri}secrets/anthropic-api-key', identity: managedIdentityId }] : []
)
var agentEnv = concat(
  [
    { name: 'APP_ENV', value: env }
    { name: 'DB_SSL', value: 'require' }
    { name: 'AGENT_DATABASE_URL', secretRef: 'database-url' }
  ],
  useClaude ? [{ name: 'ANTHROPIC_API_KEY', secretRef: 'anthropic-api-key' }] : []
)

resource agent 'Microsoft.App/containerApps@2024-03-01' = {
  name: '${namePrefix}-${env}-agent'
  location: location
  tags: tags
  identity: identityConfig
  properties: {
    managedEnvironmentId: caeId
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: { external: false, targetPort: 8100, transport: 'auto' }
      registries: registries
      secrets: agentSecrets
    }
    template: {
      containers: [
        {
          name: 'agent'
          image: agentImage
          resources: { cpu: json('0.5'), memory: '1Gi' }
          env: agentEnv
        }
      ]
      scale: { minReplicas: 0, maxReplicas: 2 }
    }
  }
}

// ---- backend-api (public) --------------------------------------------------
resource api 'Microsoft.App/containerApps@2024-03-01' = {
  name: '${namePrefix}-${env}-api'
  location: location
  tags: tags
  identity: identityConfig
  properties: {
    managedEnvironmentId: caeId
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: { external: true, targetPort: 8000, transport: 'auto' }
      registries: registries
      secrets: [
        { name: 'database-url', keyVaultUrl: '${keyVaultUri}secrets/backend-database-url', identity: managedIdentityId }
        { name: 'jwt-secret', keyVaultUrl: '${keyVaultUri}secrets/jwt-secret', identity: managedIdentityId }
      ]
    }
    template: {
      containers: [
        {
          name: 'api'
          image: backendImage
          resources: { cpu: json('0.5'), memory: '1Gi' }
          env: [
            { name: 'APP_ENV', value: env }
            { name: 'AUTH_MODE', value: 'dev' } // flip to 'entra' once Entra is wired
            { name: 'DB_SSL', value: 'require' }
            { name: 'DATABASE_URL', secretRef: 'database-url' }
            { name: 'JWT_SECRET', secretRef: 'jwt-secret' }
            { name: 'AGENT_URL', value: 'https://${agentInternalFqdn}' }
            { name: 'EXTRA_CORS_ORIGINS', value: 'https://${frontendFqdn}' }
          ]
        }
      ]
      scale: { minReplicas: 1, maxReplicas: 3 }
    }
  }
}

// ---- frontend (public, static nginx) --------------------------------------
resource web 'Microsoft.App/containerApps@2024-03-01' = {
  name: '${namePrefix}-${env}-web'
  location: location
  tags: tags
  identity: identityConfig
  properties: {
    managedEnvironmentId: caeId
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: { external: true, targetPort: 80, transport: 'auto' }
      registries: registries
    }
    template: {
      containers: [
        {
          name: 'web'
          image: frontendImage // built with VITE_API_URL=https://<apiFqdn>
          resources: { cpu: json('0.25'), memory: '0.5Gi' }
        }
      ]
      scale: { minReplicas: 1, maxReplicas: 2 }
    }
  }
}

// ---- migration / seed job (manual trigger) --------------------------------
// Runs the schema + role creation + housing load against Azure Postgres.
// Follow-up: replace the init-SQL image with Alembic migrations.
resource migrate 'Microsoft.App/jobs@2024-03-01' = {
  name: '${namePrefix}-${env}-migrate'
  location: location
  tags: tags
  identity: identityConfig
  properties: {
    environmentId: caeId
    configuration: {
      triggerType: 'Manual'
      replicaTimeout: 600
      replicaRetryLimit: 1
      registries: registries
      secrets: [
        { name: 'admin-url', keyVaultUrl: '${keyVaultUri}secrets/admin-database-url', identity: managedIdentityId }
        { name: 'app-user-pw', keyVaultUrl: '${keyVaultUri}secrets/app-user-password', identity: managedIdentityId }
        { name: 'agent-ro-pw', keyVaultUrl: '${keyVaultUri}secrets/agent-ro-password', identity: managedIdentityId }
      ]
    }
    template: {
      containers: [
        {
          name: 'migrate'
          image: jobImage
          resources: { cpu: json('0.5'), memory: '1Gi' }
          env: [
            { name: 'ADMIN_DATABASE_URL', secretRef: 'admin-url' }
            { name: 'APP_USER_PW', secretRef: 'app-user-pw' }
            { name: 'AGENT_RO_PW', secretRef: 'agent-ro-pw' }
            { name: 'PGSSLMODE', value: 'require' }
          ]
        }
      ]
    }
  }
}

output frontendUrl string = 'https://${frontendFqdn}'
output apiUrl string = 'https://${apiFqdn}'
