// Key Vault — holds app secrets (DB password, Anthropic key). The managed
// identity gets read access; Container Apps reference secrets by URI.
param namePrefix string
param env string
param location string
param tags object
param principalId string

@secure()
param postgresAdminPassword string
@secure()
param anthropicApiKey string = ''

var kvName = toLower('${namePrefix}-${env}-kv-${uniqueString(resourceGroup().id)}')

resource kv 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: substring(kvName, 0, min(length(kvName), 24)) // KV name max 24 chars
  location: location
  tags: tags
  properties: {
    sku: { family: 'A', name: 'standard' }
    tenantId: subscription().tenantId
    enableRbacAuthorization: true
    enableSoftDelete: true
  }
}

// Key Vault Secrets User for the managed identity.
var kvSecretsUserRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '4633458b-17de-408a-b874-0445c86b69e6'
)
resource kvRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(kv.id, principalId, 'kvsecrets')
  scope: kv
  properties: {
    roleDefinitionId: kvSecretsUserRoleId
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}

resource dbSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: kv
  name: 'postgres-admin-password'
  properties: { value: postgresAdminPassword }
}

resource anthropicSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = if (!empty(anthropicApiKey)) {
  parent: kv
  name: 'anthropic-api-key'
  properties: { value: anthropicApiKey }
}

output vaultUri string = kv.properties.vaultUri
output name string = kv.name
