{{/*
=======================================================
Astronomer Helm Chart - Helper Templates
=======================================================
*/}}

{{/*
Generate fullname for prelogin
*/}}
{{- define "astronomer.fullname.prelogin" -}}
{{- .Values.prelogin.fullname | default "astronomer-airflow-prelogin" -}}
{{- end -}}

{{/*
Generate name for prelogin
*/}}
{{- define "astronomer.name.prelogin" -}}
{{- .Values.prelogin.name | default "astronomer-airflow-prelogin" -}}
{{- end -}}

{{/*
Common labels for prelogin
*/}}
{{- define "astronomer.labels.prelogin" -}}
app.kubernetes.io/name: {{ include "astronomer.name.prelogin" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{/*
Generate name for web
*/}}
{{- define "astronomer.name.web" -}}
{{- printf "%s-airflow-web" .Release.Name -}}
{{- end -}}

{{/*
Common labels for web
*/}}
{{- define "astronomer.labels.web" -}}
app.kubernetes.io/name: {{ include "astronomer.name.web" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: web
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{/*
Generate domain from ingress config
Usage: 
  {{ include "astronomer.domain" (dict "Release" .Release "Values" .Values "Ingress" .Values.web.ingress) }}
*/}}
{{- define "astronomer.domain" -}}
{{- $ingress := .Ingress -}}
{{- $rootDomain := "" -}}
{{- if $ingress -}}
  {{- $rootDomain = $ingress.rootDomain | default .Values.ingress.rootDomain -}}
{{- else -}}
  {{- $rootDomain = .Values.ingress.rootDomain -}}
{{- end -}}
{{- if $rootDomain -}}
{{- printf "%s.%s" .Release.Name $rootDomain -}}
{{- else -}}
{{- .Release.Name -}}
{{- end -}}
{{- end -}}

{{/*
Service name for web
*/}}
{{- define "astronomer.service.web" -}}
{{- printf "%s-airflow-web" .Release.Name -}}
{{- end -}}

{{/*
Generate full service name with port for web
Usage: {{ include "astronomer.service.web.fullname" . }}
*/}}
{{- define "astronomer.service.web.fullname" -}}
{{- $serviceName := include "astronomer.service.web" . -}}
{{- $port := .Values.web.port | default 8080 -}}
{{- printf "%s:%v" $serviceName $port -}}
{{- end -}}

{{/*
Common annotations
*/}}
{{- define "astronomer.annotations" -}}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version | replace "+" "_" }}
{{- end -}}

{{/*
Generate fullname
*/}}
{{- define "astronomer.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
Create chart name and version as used by the chart label
*/}}
{{- define "astronomer.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Common selector labels
*/}}
{{- define "astronomer.selectorLabels" -}}
app.kubernetes.io/name: {{ include "astronomer.fullname" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{/*
Common labels
*/}}
{{- define "astronomer.labels" -}}
helm.sh/chart: {{ include "astronomer.chart" . }}
{{ include "astronomer.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}
