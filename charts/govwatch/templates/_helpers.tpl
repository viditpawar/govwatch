{{- define "govwatch.name" -}}
{{- .Chart.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "govwatch.fullname" -}}
{{- if contains .Chart.Name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name .Chart.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "govwatch.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{ include "govwatch.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "govwatch.selectorLabels" -}}
app.kubernetes.io/name: {{ include "govwatch.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "govwatch.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "govwatch.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{- define "govwatch.image" -}}
{{- if .Values.image.digest -}}
{{- printf "%s@%s" .Values.image.repository .Values.image.digest -}}
{{- else -}}
{{- printf "%s:%s" .Values.image.repository (default .Chart.AppVersion .Values.image.tag) -}}
{{- end -}}
{{- end -}}

{{- define "govwatch.dbClusterName" -}}
{{- printf "%s-db" (include "govwatch.fullname" .) -}}
{{- end -}}

{{- define "govwatch.apiKeysSecret" -}}
{{- default (printf "%s-api-keys" (include "govwatch.fullname" .)) .Values.apiKeys.existingSecret -}}
{{- end -}}

{{- define "govwatch.databaseSecret" -}}
{{- if .Values.database.cnpg.enabled -}}
{{- printf "%s-app" (include "govwatch.dbClusterName" .) -}}
{{- else -}}
{{- required "database.existingSecret is required when database.cnpg.enabled is false" .Values.database.existingSecret -}}
{{- end -}}
{{- end -}}

{{/* env shared by the migrate init container and the worker. secrets aren't in here:
they're mounted as files (see govwatch.secretsVolume) so they stay out of the process
environment, child processes and crash dumps */}}
{{- define "govwatch.env" -}}
- name: GOVWATCH_SECRETS_DIR
  value: /var/run/secrets/govwatch
- name: GOVWATCH_POLL_INTERVAL_SECONDS
  value: {{ .Values.config.pollIntervalSeconds | quote }}
- name: GOVWATCH_BACKFILL_DAYS
  value: {{ .Values.config.backfillDays | quote }}
- name: GOVWATCH_AUDIT_INTERVAL_SECONDS
  value: {{ .Values.config.auditIntervalSeconds | quote }}
- name: GOVWATCH_AUDIT_WINDOW_DAYS
  value: {{ .Values.config.auditWindowDays | quote }}
- name: GOVWATCH_AUDIT_AUTO_REPAIR
  value: {{ .Values.config.auditAutoRepair | quote }}
- name: GOVWATCH_LOG_LEVEL
  value: {{ .Values.config.logLevel | quote }}
- name: GOVWATCH_METRICS_HOST
  value: "0.0.0.0"
- name: GOVWATCH_METRICS_PORT
  value: "9100"
{{- end -}}

{{/* api keys and the database uri, projected into one read-only directory with the file
names the settings module expects */}}
{{- define "govwatch.secretsVolume" -}}
- name: secrets
  projected:
    defaultMode: 0440
    sources:
      - secret:
          name: {{ include "govwatch.apiKeysSecret" . }}
          items:
            - key: congress-api-key
              path: GOVWATCH_CONGRESS_API_KEY
            - key: regulations-api-key
              path: GOVWATCH_REGULATIONS_API_KEY
      - secret:
          name: {{ include "govwatch.databaseSecret" . }}
          items:
            - key: {{ .Values.database.uriKey }}
              path: GOVWATCH_DATABASE_URL
{{- end -}}
