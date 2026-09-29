{{- define "gateway.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name .Chart.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "gateway.labels" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Values.image.tag | default .Chart.AppVersion }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app: {{ .Chart.Name }}
# --- FinOps cost-allocation labels (Kubecost / Cloudability) ---
finops.io/cost-center: {{ .Values.global.finops.costCenter | default "unassigned" }}
finops.io/budget-owner: {{ .Values.global.finops.budgetOwner | default "unassigned" }}
finops.io/workload: {{ .Values.global.finops.workload | default "ai-platform" }}
finops.io/environment: {{ .Values.global.environment | default "dev" }}
{{- end -}}