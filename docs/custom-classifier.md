# Custom Classifier — Train and Enable

The app supports two split strategies for an uploaded merged PDF:

| Mode         | How it splits                                        | Split-pass cost / 1k pages |
|--------------|------------------------------------------------------|----------------------------|
| `heuristic`  | One `prebuilt-layout` pass + keyword classification  | **$10**                    |
| `classifier` | Custom DI classifier in a single call                | **$3**                     |

Per-segment extraction (`prebuilt-tax.us.w2`, `prebuilt-idDocument`, …) runs the same way in both modes.

## 1. Generate a training set

```powershell
python -m app.sample.generate_training_set --count 8 --out samples/training
```

Produces:

```
samples/training/
  paystub/             paystub_001.pdf … paystub_008.pdf
  bank_statement/
  w2/
  passport/
  drivers_license/
```

Folder names are the **class labels** DI Studio will use.

## 2. Upload to Storage

The deployment already creates a storage account (`st<env><suffix>`).
Create a container `classifier-training` and upload the `samples/training/` tree:

```powershell
$env:AZURE_STORAGE_ACCOUNT = (azd env get-values | Select-String 'AZURE_STORAGE_ACCOUNT').ToString().Split('=')[1].Trim('"')
az storage container create --account-name $env:AZURE_STORAGE_ACCOUNT --name classifier-training --auth-mode login
az storage blob upload-batch --account-name $env:AZURE_STORAGE_ACCOUNT -d classifier-training -s samples/training --auth-mode login
```

## 3. Train the classifier in DI Studio

1. Open <https://documentintelligence.ai.azure.com/studio>.
2. **Custom models → Custom classification model → Create project**.
3. Select your DI resource (`di-<env>-<suffix>`).
4. Connect the `classifier-training` container — each subfolder becomes a class automatically.
5. **Train** (a couple of minutes for ~8 docs/class).
6. Copy the resulting **Model ID**.

## 4. Wire it into the app

```powershell
azd env set CLASSIFIER_ID <modelId>
azd deploy
```

The Container App restarts with `CLASSIFIER_ID` set; the home page shows
`configured (<id>)` and the **Custom classifier** / **Compare both** radios become functional.

## 5. Try it

Open the app URL and upload your PDF with mode set to:

- **Heuristic splitter** — current behaviour.
- **Custom classifier** — one DI call replaces the layout + keyword logic.
- **Compare both** — runs both pipelines and returns a `comparison` block:

```json
"comparison": {
  "totalCostUsd":   { "heuristic": 0.00056, "classifier": 0.00021,
                      "savingsUsd": 0.00035, "savingsPct": 62.5 },
  "billedPages":    { "heuristic": 12, "classifier": 12 },
  "segmentCount":   { "heuristic": 5, "classifier": 5 },
  "segmentDocTypes":{ "heuristic": ["paystub","paystub","bank_statement", ...],
                      "classifier":["paystub","bank_statement","w2","passport","drivers_license"] }
}
```

## 6. Per-tenant cost telemetry

`loadtest/cost-allocation.kql` now groups by `splitStrategy`, so you can A/B
the strategies in App Insights:

```
tenantId         splitStrategy    model                      pages   estCostUsd
demo-tenant      heuristic        prebuilt-layout            120     1.20
demo-tenant      heuristic        prebuilt-tax.us.w2          10     0.50
demo-tenant      classifier       <classifier-id>            120     0.36
demo-tenant      classifier       prebuilt-tax.us.w2          10     0.50
```
