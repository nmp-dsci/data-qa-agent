// Azure Database for PostgreSQL Flexible Server — one server, one database.
// pgvector + pgcrypto are allow-listed so the app can CREATE EXTENSION.
param namePrefix string
param env string
param location string
param tags object
param administratorLogin string = 'pgadmin'
@secure()
param administratorPassword string
param databaseName string = 'dataqa'

resource pg 'Microsoft.DBforPostgreSQL/flexibleServers@2023-12-01-preview' = {
  name: '${namePrefix}-${env}-pg'
  location: location
  tags: tags
  sku: {
    name: 'Standard_B1ms' // burstable, cheapest — right for dev
    tier: 'Burstable'
  }
  properties: {
    version: '16'
    administratorLogin: administratorLogin
    administratorLoginPassword: administratorPassword
    storage: { storageSizeGB: 32 }
    backup: { backupRetentionDays: 7, geoRedundantBackup: 'Disabled' }
    highAvailability: { mode: 'Disabled' }
  }
}

resource db 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2023-12-01-preview' = {
  parent: pg
  name: databaseName
  properties: { charset: 'UTF8', collation: 'en_US.utf8' }
}

// Allow the pgvector + pgcrypto extensions to be created.
resource extensions 'Microsoft.DBforPostgreSQL/flexibleServers/configurations@2023-12-01-preview' = {
  parent: pg
  name: 'azure.extensions'
  properties: { value: 'VECTOR,PGCRYPTO', source: 'user-override' }
}

// Dev-only: allow other Azure services (Container Apps) to reach the server.
// Tighten to VNet/Private Endpoint for staging/prod.
resource allowAzure 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2023-12-01-preview' = {
  parent: pg
  name: 'AllowAllAzureServices'
  properties: { startIpAddress: '0.0.0.0', endIpAddress: '0.0.0.0' }
}

output fqdn string = pg.properties.fullyQualifiedDomainName
output databaseName string = databaseName
output administratorLogin string = administratorLogin
