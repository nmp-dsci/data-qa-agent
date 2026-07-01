// Writes connection-string + JWT secrets into the existing Key Vault, once the
// Postgres FQDN is known. Container Apps reference these by URI (never inline).
// NOTE: the deploying principal needs the "Key Vault Secrets Officer" role.
param kvName string
param pgFqdn string
param databaseName string
param adminLogin string
@secure()
param adminPassword string
@secure()
param appUserPassword string
@secure()
param agentRoPassword string
@secure()
param jwtSecret string

resource kv 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: kvName
}

// Admin URL (psql) — used by the migration job to create schema + roles + extensions.
resource adminUrl 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: kv
  name: 'admin-database-url'
  properties: {
    value: 'postgresql://${adminLogin}:${adminPassword}@${pgFqdn}:5432/${databaseName}'
  }
}

// App role (read/write) — backend-api.
resource backendUrl 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: kv
  name: 'backend-database-url'
  properties: {
    value: 'postgresql+asyncpg://app_user:${appUserPassword}@${pgFqdn}:5432/${databaseName}'
  }
}

// Read-only role — data-agent.
resource agentUrl 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: kv
  name: 'agent-database-url'
  properties: {
    value: 'postgresql+asyncpg://agent_ro:${agentRoPassword}@${pgFqdn}:5432/${databaseName}'
  }
}

resource jwt 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: kv
  name: 'jwt-secret'
  properties: { value: jwtSecret }
}

// Role passwords — the migration job uses these to (re)set the app roles so they
// match the connection strings above.
resource appUserPw 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: kv
  name: 'app-user-password'
  properties: { value: appUserPassword }
}

resource agentRoPw 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: kv
  name: 'agent-ro-password'
  properties: { value: agentRoPassword }
}
