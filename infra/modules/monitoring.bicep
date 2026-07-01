// Log Analytics workspace — backs Container Apps logs + metrics.
param namePrefix string
param env string
param location string
param tags object

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${namePrefix}-${env}-logs'
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

output workspaceId string = logs.id
output customerId string = logs.properties.customerId
@secure()
output primarySharedKey string = logs.listKeys().primarySharedKey
