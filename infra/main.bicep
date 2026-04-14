// Azure Container Apps deployment for Azure SLA Dashboard
// Deploy with: az deployment group create -g <resource-group> -f infra/main.bicep

@description('Name prefix for all resources')
param namePrefix string = 'azsla'

@description('Azure region for resources')
param location string = resourceGroup().location

@description('Container image to deploy')
param containerImage string = 'ghcr.io/your-org/azure-sla-dashboard:latest'

@description('Azure subscription IDs to monitor (comma-separated)')
@secure()
param azureSubscriptionIds string = ''

@description('ACR server name')
param acrServer string = ''

@description('ACR username')
param acrUsername string = ''

@description('ACR password')
@secure()
param acrPassword string = ''

@description('Log Analytics workspace ID for Container Apps')
param logAnalyticsWorkspaceId string = ''

@description('Log Analytics workspace shared key')
@secure()
param logAnalyticsSharedKey string = ''

// Variables
var containerAppEnvName = '${namePrefix}-env'
var containerAppName = '${namePrefix}-app'
var userAssignedIdentityName = '${namePrefix}-identity'

// User Assigned Managed Identity for Azure access
resource managedIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: userAssignedIdentityName
  location: location
}

// Container Apps Environment
resource containerAppEnv 'Microsoft.App/managedEnvironments@2023-05-01' = {
  name: containerAppEnvName
  location: location
  properties: {
    appLogsConfiguration: logAnalyticsWorkspaceId != '' ? {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalyticsWorkspaceId
        sharedKey: logAnalyticsSharedKey
      }
    } : {
      destination: 'azure-monitor'
    }
  }
}

// Container App
resource containerApp 'Microsoft.App/containerApps@2023-05-01' = {
  name: containerAppName
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${managedIdentity.id}': {}
    }
  }
  properties: {
    managedEnvironmentId: containerAppEnv.id
    configuration: {
      ingress: {
        external: true
        targetPort: 8000
        transport: 'http'
        corsPolicy: {
          allowedOrigins: ['*']
        }
      }
      registries: acrServer != '' ? [
        {
          server: acrServer
          username: acrUsername
          passwordSecretRef: 'acr-password'
        }
      ] : []
      secrets: [
        {
          name: 'azure-subscription-ids'
          value: azureSubscriptionIds
        }
        {
          name: 'acr-password'
          value: acrPassword
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'sla-dashboard'
          image: containerImage
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: [
            {
              name: 'AZURE_CLIENT_ID'
              value: managedIdentity.properties.clientId
            }
            {
              name: 'AZURE_SUBSCRIPTION_IDS'
              secretRef: 'azure-subscription-ids'
            }
            {
              name: 'DATABASE_URL'
              value: 'sqlite+aiosqlite:////data/sla_data.db'
            }
            {
              name: 'COLLECTION_ENABLED'
              value: 'true'
            }
            {
              name: 'COLLECTION_INTERVAL_HOURS'
              value: '6'
            }
            {
              name: 'LOG_LEVEL'
              value: 'INFO'
            }
          ]
          volumeMounts: [
            {
              volumeName: 'data'
              mountPath: '/data'
            }
          ]
          probes: [
            {
              type: 'Liveness'
              httpGet: {
                path: '/api/docs'
                port: 8000
              }
              initialDelaySeconds: 10
              periodSeconds: 30
            }
            {
              type: 'Readiness'
              httpGet: {
                path: '/api/docs'
                port: 8000
              }
              initialDelaySeconds: 5
              periodSeconds: 10
            }
          ]
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 3
        rules: [
          {
            name: 'http-scaling'
            http: {
              metadata: {
                concurrentRequests: '50'
              }
            }
          }
        ]
      }
      volumes: [
        {
          name: 'data'
          storageType: 'EmptyDir'
        }
      ]
    }
  }
}

// Outputs
output containerAppUrl string = 'https://${containerApp.properties.configuration.ingress.fqdn}'
output managedIdentityId string = managedIdentity.id
output managedIdentityClientId string = managedIdentity.properties.clientId
output managedIdentityPrincipalId string = managedIdentity.properties.principalId
