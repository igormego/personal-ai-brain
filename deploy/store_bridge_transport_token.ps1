# Owner-run only. Never pass a token as an argument or paste it into chat.
$ErrorActionPreference='Stop'
$root=Join-Path $env:LOCALAPPDATA 'PersonalAIBrain\bridge-v01\transport'
$target=Join-Path $root 'github-token'
$temporary=Join-Path $root ('credential-'+[guid]::NewGuid().ToString()+'.tmp')
function Protect-Path($Path,$Directory){
    $sid=[Security.Principal.WindowsIdentity]::GetCurrent().User
    if($Directory){$acl=[Security.AccessControl.DirectorySecurity]::new()}else{$acl=[Security.AccessControl.FileSecurity]::new()}
    $acl.SetOwner($sid);$acl.SetAccessRuleProtection($true,$false)
    foreach($id in @($sid.Value,'S-1-5-18','S-1-5-32-544')){
        $identity=[Security.Principal.SecurityIdentifier]::new($id)
        if($Directory){$rule=[Security.AccessControl.FileSystemAccessRule]::new($identity,'FullControl','ContainerInherit,ObjectInherit','None','Allow')}
        else{$rule=[Security.AccessControl.FileSystemAccessRule]::new($identity,'FullControl','Allow')}
        $acl.AddAccessRule($rule)
    }
    Set-Acl -LiteralPath $Path -AclObject $acl
}
try {
    New-Item -ItemType Directory -Path $root -Force | Out-Null
    if((Get-Item -LiteralPath $root).Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Unsafe path'}
    Protect-Path $root $true
    if(Test-Path -LiteralPath $target){throw 'Credential already exists; stop before replacement'}
    $secure=Read-Host 'Dedicated Windows transport PAT (hidden)' -AsSecureString
    $ptr=[Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try{$value=[Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
        if($value -notmatch '^github_pat_[A-Za-z0-9_]+$'){throw 'Unexpected format'}
        [IO.File]::WriteAllText($temporary,$value,[Text.UTF8Encoding]::new($false))
        Protect-Path $temporary $false
        [IO.File]::Move($temporary,$target)
    } finally {$value=$null;[Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr);$secure.Dispose()}
    Write-Output 'Dedicated transport credential stored with restricted ACL. No value displayed.'
} catch { Write-Output 'Credential setup stopped; no exception details displayed.'; exit 1 }
finally { if(Test-Path -LiteralPath $temporary){Remove-Item -LiteralPath $temporary -Force} }
