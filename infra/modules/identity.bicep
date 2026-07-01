// User-assigned managed identity used by every Container App to pull images
// from ACR and read secrets from Key Vault — so no passwords live in the app.
param namePrefix string
param env string
param location string
param tags object
param acrId string

resource mi 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${namePrefix}-${env}-mi'
  location: location
  tags: tags
}

// AcrPull on the registry.
var acrPullRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '7f951dda-4ed3-4680-a7ca-43fe172d538d'
)
resource acrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acrId, mi.id, 'acrpull')
  scope: resourceGroup()
  properties: {
    roleDefinitionId: acrPullRoleId
    principalId: mi.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

output id string = mi.id
output principalId string = mi.properties.principalId
output clientId string = mi.properties.clientId
