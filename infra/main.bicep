// Subscription-scope deployment that targets the existing RG `IDP-rg`.
// Provisions: Document Intelligence (S0), Log Analytics, App Insights,
// Container Apps Environment + App, Container Registry, Storage account.
// All resources tagged for Cost Management filtering.

targetScope = 'subscription'

@description('Existing resource group name.')
param resourceGroupName string = 'IDP-rg'

@description('Azure region (Document Intelligence v4.0 GA regions: eastus, westus2, westeurope, etc.).')
param location string = 'eastus'

@description('Short environment name token used in resource names.')
@minLength(2)
@maxLength(8)
param envName string = 'idpdemo'

@description('Container image to deploy. Set by `azd deploy`.')
param apiImage string = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'

@description('Custom classifier id for the alternative split strategy. Empty disables that mode.')
param classifierId string = ''

var tags = {
  app: 'idp-demo'
  env: 'demo'
  costcenter: 'loan-ops'
  'azd-env-name': envName
}

resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' existing = {
  name: resourceGroupName
}

module main 'modules/resources.bicep' = {
  name: 'idp-resources'
  scope: rg
  params: {
    location: location
    envName: envName
    tags: tags
    apiImage: apiImage
    classifierId: classifierId
  }
}

output API_URL string = main.outputs.apiUrl
output DI_ENDPOINT string = main.outputs.diEndpoint
output APPLICATIONINSIGHTS_CONNECTION_STRING string = main.outputs.appInsightsConnectionString
output AZURE_CONTAINER_REGISTRY_ENDPOINT string = main.outputs.acrLoginServer
output AZURE_RESOURCE_GROUP string = resourceGroupName
