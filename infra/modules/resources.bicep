// RG-scoped resources for the IDP demo.

param location string
param envName string
param tags object
param apiImage string
param classifierId string = ''

var suffix = uniqueString(resourceGroup().id, envName)

// ---------- Observability ----------
resource law 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'log-${envName}-${suffix}'
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

resource appi 'Microsoft.Insights/components@2020-02-02' = {
  name: 'appi-${envName}-${suffix}'
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: law.id
  }
}

// ---------- Document Intelligence (AIServices kind) ----------
// Unified AIServices account: same DI REST surface, plus room for future
// Foundry features (Content Understanding, OpenAI deployments, etc.).
resource di 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
  name: 'ai-${envName}-${suffix}'
  location: location
  tags: tags
  kind: 'AIServices'
  sku: { name: 'S0' }
  identity: { type: 'SystemAssigned' }
  properties: {
    customSubDomainName: 'ai-${envName}-${suffix}'
    publicNetworkAccess: 'Enabled'
    disableLocalAuth: true
  }
}

// ---------- Storage (uploaded PDFs / artifacts) ----------
resource sa 'Microsoft.Storage/storageAccounts@2024-01-01' = {
  name: toLower(replace('st${envName}${suffix}', '-', ''))
  location: location
  tags: tags
  kind: 'StorageV2'
  sku: { name: 'Standard_LRS' }
  properties: {
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
  }
}

// ---------- Container Registry ----------
resource acr 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' = {
  name: toLower(replace('acr${envName}${suffix}', '-', ''))
  location: location
  tags: tags
  sku: { name: 'Basic' }
  properties: {
    adminUserEnabled: false
  }
}

// ---------- Managed identity for the Container App ----------
resource uami 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-${envName}-${suffix}'
  location: location
  tags: tags
}

// AcrPull for the UAMI
var acrPullRoleId = '7f951dda-4ed3-4680-a7ca-43fe172d538d'
resource acrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, uami.id, 'acrpull')
  scope: acr
  properties: {
    principalId: uami.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPullRoleId)
  }
}

// Cognitive Services User on DI for the UAMI
var cogUserRoleId = 'a97b65f3-24c7-4388-baec-2e87135dc908'
resource diRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(di.id, uami.id, 'cogsvcuser')
  scope: di
  properties: {
    principalId: uami.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cogUserRoleId)
  }
}

// ---------- Container Apps Environment + App ----------
resource cae 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: 'cae-${envName}-${suffix}'
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: law.properties.customerId
        sharedKey: law.listKeys().primarySharedKey
      }
    }
  }
}

resource api 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'ca-api-${envName}'
  location: location
  tags: union(tags, { 'azd-service-name': 'api' })
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${uami.id}': {} }
  }
  properties: {
    managedEnvironmentId: cae.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto'
      }
      registries: [
        {
          server: acr.properties.loginServer
          identity: uami.id
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'api'
          image: apiImage
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: [
            { name: 'DI_ENDPOINT', value: di.properties.endpoint }
            { name: 'AZURE_CLIENT_ID', value: uami.properties.clientId }
            { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appi.properties.ConnectionString }
            { name: 'TENANT_ID_HEADER', value: 'x-tenant-id' }
            { name: 'CLASSIFIER_ID', value: classifierId }
            { name: 'PORT', value: '8000' }
          ]
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 10
        rules: [
          {
            name: 'http-rule'
            http: { metadata: { concurrentRequests: '20' } }
          }
        ]
      }
    }
  }
  dependsOn: [ acrPull, diRole ]
}

output apiUrl string = 'https://${api.properties.configuration.ingress.fqdn}'
output diEndpoint string = di.properties.endpoint
output appInsightsConnectionString string = appi.properties.ConnectionString
output acrLoginServer string = acr.properties.loginServer
output storageAccountName string = sa.name
