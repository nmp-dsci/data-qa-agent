// Container Apps managed environment. Split out so its default domain is known
// before we build the frontend image (which needs the API URL at build time).
param namePrefix string
param env string
param location string
param tags object
param logWorkspaceCustomerId string
@secure()
param logWorkspaceSharedKey string

resource menv 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: '${namePrefix}-${env}-cae'
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logWorkspaceCustomerId
        sharedKey: logWorkspaceSharedKey
      }
    }
  }
}

output id string = menv.id
output defaultDomain string = menv.properties.defaultDomain
