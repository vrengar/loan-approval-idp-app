param(
    [string]$ClassifierId = "idp-loan-docs-v1",
    [string]$Container = "classifier-training",
    [string]$ResourceGroup = "IDP-rg",
    [Parameter(Mandatory = $true)][string]$DiAccount,
    [Parameter(Mandatory = $true)][string]$StorageAccount
)

$ErrorActionPreference = "Stop"
$endpoint = "https://$DiAccount.cognitiveservices.azure.com"
$api = "api-version=2023-07-31"

Write-Host "Generating user-delegation SAS for $StorageAccount/$Container ..."
$expiry = (Get-Date).AddHours(4).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
$sas = az storage container generate-sas --account-name $StorageAccount -n $Container --permissions rl --expiry $expiry --https-only --auth-mode login --as-user -o tsv
$url = "https://$StorageAccount.blob.core.windows.net/$Container" + "?" + $sas

$body = @"
{
  "classifierId": "$ClassifierId",
  "description": "Loan docs classifier (paystub/w2/bank/passport/dl)",
  "docTypes": {
    "paystub": {"azureBlobSource": {"containerUrl": "$url", "prefix": "paystub/"}},
    "w2": {"azureBlobSource": {"containerUrl": "$url", "prefix": "w2/"}},
    "bank_statement": {"azureBlobSource": {"containerUrl": "$url", "prefix": "bank_statement/"}},
    "passport": {"azureBlobSource": {"containerUrl": "$url", "prefix": "passport/"}},
    "drivers_license": {"azureBlobSource": {"containerUrl": "$url", "prefix": "drivers_license/"}}
  }
}
"@
$body | Out-File -Encoding ascii build.json
[System.IO.File]::WriteAllText("$PWD\build.json", $body)

$tok = az account get-access-token --resource "https://cognitiveservices.azure.com" --query accessToken -o tsv

Write-Host "Deleting any existing classifier $ClassifierId ..."
& curl.exe -s -o $null -w "  DELETE=%{http_code}`n" -X DELETE "$endpoint/formrecognizer/documentClassifiers/$ClassifierId`?$api" -H "Authorization: Bearer $tok"

Write-Host "Submitting build ..."
& curl.exe -s -D headers.txt -o response.txt -w "  BUILD=%{http_code}`n" -X POST "$endpoint/formrecognizer/documentClassifiers`:build?$api" -H "Authorization: Bearer $tok" -H "Content-Type: application/json" --data "@build.json"
$opLine = (Get-Content headers.txt | Select-String "operation-location" | Select-Object -First 1)
if (-not $opLine) {
    Write-Host "FAILED. Response:"
    Get-Content response.txt
    exit 1
}
$opUrl = ($opLine -replace "operation-location:\s*","").Trim()
Write-Host "Operation: $opUrl"

do {
    Start-Sleep 15
    $tok = az account get-access-token --resource "https://cognitiveservices.azure.com" --query accessToken -o tsv
    & curl.exe -s -o op.json -X GET $opUrl -H "Authorization: Bearer $tok"
    $op = Get-Content op.json -Raw | ConvertFrom-Json
    Write-Host ("  {0} status={1} pct={2}" -f (Get-Date -Format HH:mm:ss), $op.status, $op.percentCompleted)
} while ($op.status -in @('running','notStarted'))

if ($op.status -eq "succeeded") {
    Write-Host ""
    Write-Host "SUCCESS. Classifier '$ClassifierId' ready."
    Write-Host ""
    Write-Host "Next:"
    Write-Host "  azd env set CLASSIFIER_ID $ClassifierId"
    Write-Host "  azd deploy"
} else {
    Write-Host "FAILED:"
    Get-Content op.json -Raw
    exit 1
}
