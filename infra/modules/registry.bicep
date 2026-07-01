// Azure Container Registry — stores the service + job images.
param namePrefix string
param env string
param location string
param tags object

// ACR names are globally unique, alphanumeric only, 5-50 chars.
var acrName = toLower('${namePrefix}${env}acr${uniqueString(resourceGroup().id)}')

resource acr 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' = {
  name: acrName
  location: location
  tags: tags
  sku: { name: 'Basic' }
  properties: {
    adminUserEnabled: false // we pull with a managed identity, not admin creds
  }
}

output loginServer string = acr.properties.loginServer
output id string = acr.id
output name string = acr.name
