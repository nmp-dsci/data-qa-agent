#!/usr/bin/env bash
set -euo pipefail

# Always run from the opensrc/ directory regardless of where the script is called from
cd "$(dirname "$0")"

# Install opensrc CLI if not present
if ! command -v opensrc &>/dev/null; then
  echo "opensrc CLI not found — installing..."
  npm install -g opensrc
fi

# Create sources.json if missing
if [ ! -f sources.json ]; then
  echo "[]" > sources.json
  echo "Created opensrc/sources.json (empty manifest — add packages and re-run)"
  exit 0
fi

# Fetch any packages listed in sources.json that aren't already present
node -e "
const sources = require('./sources.json');
const { execSync } = require('child_process');
const fs = require('fs');

if (sources.length === 0) {
  console.log('sources.json is empty — nothing to fetch.');
  process.exit(0);
}

sources.forEach(({ repo, version }) => {
  const name = repo.split('/')[1];
  if (fs.existsSync(name)) {
    console.log('  already present: ' + name);
  } else {
    console.log('  fetching ' + repo + '@' + version + '...');
    execSync('opensrc fetch ' + repo + '@' + version, { stdio: 'inherit' });
    const srcPath = execSync('opensrc path ' + repo + '@' + version).toString().trim();
    console.log('  copying into opensrc/' + name + '/...');
    execSync('cp -r ' + srcPath + ' ' + name);
  }
});

console.log('Done.');
"
