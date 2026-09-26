#Requires -RunAsAdministrator
param([Parameter(Mandatory = $true)][string]$Profile)
$ErrorActionPreference = 'Stop'
$exe = Join-Path $env:ProgramFiles 'WireGuard\wireguard.exe'
if (!(Test-Path $exe)) { throw 'Install WireGuard: https://www.wireguard.com/install/' }
$source = (Resolve-Path -LiteralPath $Profile).Path
$content = [IO.File]::ReadAllText($source)
# Reject hooks, default routes, extra peers and unexpected directives before elevated import.
$section = ''; $fields = @{}
foreach ($raw in ($content -split "`r?`n")) {
    $line = ($raw -split '#', 2)[0].Trim()
    if (!$line) { continue }
    if ($line -match '^\[(Interface|Peer)\]$') {
        $section = $Matches[1]
        if ($fields.ContainsKey($section)) { throw 'Duplicate section' }
        $fields[$section] = @{}
    } elseif ($line -match '^([A-Za-z]+)\s*=\s*(.+)$' -and $section) {
        $key = $Matches[1]; $value = $Matches[2]
        $allowed = if ($section -eq 'Interface') { @('PrivateKey','Address','DNS','MTU') } else { @('PublicKey','AllowedIPs','Endpoint','PersistentKeepalive') }
        if ($key -notin $allowed -or $fields[$section].ContainsKey($key)) { throw 'Unsupported or duplicate setting' }
        $fields[$section][$key] = $value
    } else { throw 'Invalid profile syntax' }
}
if ($fields.Count -ne 2) { throw 'Expected Interface and Peer' }
if ($fields.Peer.AllowedIPs -notmatch '^fdaa:[0-9a-f]{1,4}:[0-9a-f]{1,4}::/48$') { throw 'Expected one Fly private /48 route' }
if ($fields.Interface.DNS -notmatch '^fdaa:[0-9a-f]{1,4}:[0-9a-f]{1,4}::3$') { throw 'Expected Fly DNS' }
$prefix = $fields.Peer.AllowedIPs -replace '::/48$', ':'
if (!$fields.Interface.DNS.StartsWith($prefix) -or !$fields.Interface.Address.StartsWith($prefix)) { throw 'Profile subnet mismatch' }
foreach ($key in @($fields.Interface.PrivateKey, $fields.Peer.PublicKey)) {
    try { $bytes = [Convert]::FromBase64String($key) } catch { throw 'Invalid key encoding' }
    if ($bytes.Length -ne 32) { throw 'Invalid key length' }
}
$legacy = Get-Service -Name 'WireGuardTunnel$samofly' -ErrorAction SilentlyContinue
$folder = Join-Path $env:ProgramData $(if ($legacy) { 'Samoverse\FlyAccess' } else { 'FlyAccess' })
$tunnelName = if ($legacy) { 'samofly' } else { 'flyaccess' }
$dest = Join-Path $folder ($tunnelName + '.conf')
$marker = Join-Path $folder '.managed'
$serviceName = 'WireGuardTunnel$' + $tunnelName
$service = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
if ((Test-Path $folder) -and !(Test-Path $marker)) { throw 'Existing folder is not managed by this script' }
if ($service -and (!(Test-Path $marker) -or !(Test-Path $dest))) { throw 'Existing tunnel is not managed by this script' }
if ($service -and [IO.File]::ReadAllText($dest) -ne $content) { throw 'Existing profile differs; explicitly remove the old tunnel before replacing it' }
if (!(Test-Path $folder)) { New-Item -ItemType Directory -Path $folder -Force | Out-Null }
if ((Get-Item $folder).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Refusing a reparse point' }
# SYSTEM and Administrators only; SIDs work on non-English Windows too.
$acl = New-Object Security.AccessControl.DirectorySecurity
$acl.SetAccessRuleProtection($true, $false)
foreach ($sid in @('S-1-5-18', 'S-1-5-32-544')) {
    $identity = New-Object Security.Principal.SecurityIdentifier($sid)
    $rule = New-Object Security.AccessControl.FileSystemAccessRule($identity, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
    $acl.AddAccessRule($rule)
}
Set-Acl -LiteralPath $folder -AclObject $acl
[IO.File]::WriteAllText($marker, 'Fly Access')
[IO.File]::WriteAllText($dest, $content, [Text.UTF8Encoding]::new($false))
if (!$service) {
    $process = Start-Process -FilePath $exe -ArgumentList @('/installtunnelservice', ('"{0}"' -f $dest)) -Wait -PassThru
    if ($process.ExitCode -ne 0) { throw 'WireGuard tunnel installation failed' }
}
Set-Service -Name $serviceName -StartupType Automatic
Start-Service -Name $serviceName
Write-Host 'Installed: autostart, private Fly traffic and system DNS through WireGuard; other traffic direct.'
Write-Host 'Check: Resolve-DnsName YOUR-APP.flycast; Resolve-DnsName example.com'
