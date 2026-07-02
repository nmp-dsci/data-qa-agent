using 'main.bicep'

// ---- Non-secret settings (safe to commit) ----
param env = 'dev'
param namePrefix = 'dataqa'
param imageTag = 'latest'
param useClaude = false

// ---- Secrets: DO NOT hardcode. Supply at deploy time via env vars ----
// e.g.  export PG_ADMIN_PW=...  and pass  -p postgresAdminPassword=$PG_ADMIN_PW
// or reference a Key Vault with getSecret(). Left as readEnvironmentVariable so
// nothing sensitive lands in git.
param postgresAdminPassword = readEnvironmentVariable('PG_ADMIN_PW')
param appUserPassword = readEnvironmentVariable('APP_USER_PW')
param agentRoPassword = readEnvironmentVariable('AGENT_RO_PW')
param jwtSecret = readEnvironmentVariable('JWT_SECRET')
param anthropicApiKey = readEnvironmentVariable('ANTHROPIC_API_KEY', '')
