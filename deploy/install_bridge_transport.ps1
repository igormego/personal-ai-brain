# Run only after the reviewed implementation is merged. Uses the interactive owner.
$ErrorActionPreference='Stop'
$repo='C:\Projects\personal-ai-brain'
$python=Join-Path $repo '.venv\Scripts\python.exe'
$pythonw=Join-Path $repo '.venv\Scripts\pythonw.exe'
$taskName='PersonalAIBrain-CodexTransport-v01'
if((git -C $repo branch --show-current) -ne 'main'){throw 'Install only reviewed main'}
& $python -m personal_ai_brain.codex_bridge_transport_runtime check
if($LASTEXITCODE -ne 0){throw 'Credential/ACL preflight failed'}
if(Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue){throw 'Task already exists; inspect before updating'}
$owner=[Security.Principal.WindowsIdentity]::GetCurrent().Name
$action=New-ScheduledTaskAction -Execute $pythonw -Argument '-m personal_ai_brain.codex_bridge_transport_runtime serve' -WorkingDirectory $repo
$trigger=New-ScheduledTaskTrigger -AtLogOn -User $owner
$principal=New-ScheduledTaskPrincipal -UserId $owner -LogonType Interactive -RunLevel Limited
$settings=New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description 'Outbound-only private Issues mailbox for accepted Local Bridge; no remote approvals.' | Out-Null
Start-ScheduledTask -TaskName $taskName
Write-Output 'User-context Local Bridge transport task installed and started.'
