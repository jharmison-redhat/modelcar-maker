{{/*
Expand the name of the chart.
*/}}
{{- define "modelcar-maker.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
We truncate at 63 chars because some Kubernetes name fields are limited to this (by the DNS naming spec).
If release name contains chart name it will be used as a full name.
*/}}
{{- define "modelcar-maker.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default (trimSuffix "-chart" .Chart.Name) .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "modelcar-maker.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "modelcar-maker.labels" -}}
helm.sh/chart: {{ include "modelcar-maker.chart" . }}
{{ include "modelcar-maker.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Config hash (for unique job naming)
*/}}
{{- define "modelcar-maker.config-hash" -}}
{{- include (print $.Template.BasePath "/configmap.yaml") . | sha256sum -}}
{{- end -}}

{{/*
Pod annotations
*/}}
{{- define "modelcar-maker.podAnnotations" -}}
checksum/config: {{ include "modelcar-maker.config-hash" . }}
{{- with .Values.podAnnotations }}
{{ toYaml . }}
{{- end }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "modelcar-maker.selectorLabels" -}}
app.kubernetes.io/name: {{ include "modelcar-maker.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Create the name of the service account to use
*/}}
{{- define "modelcar-maker.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "modelcar-maker.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Create the rendered ImageStream name
*/}}
{{- define "modelcar-maker.image-stream-name" -}}
{{- default (include "modelcar-maker.fullname" .) .Values.modelcar.imageStream.name }}
{{- end }}

{{/*
Create the job image name
*/}}
{{- define "modelcar-maker.image-name" -}}
  {{- .Values.image.registry -}}
  /
  {{- .Values.image.repository -}}
  {{- if .Values.image.manifestHash -}}
    @{{ .Values.image.manifestHash -}}
  {{- else -}}
    :
    {{- default .Chart.AppVersion .Values.image.tag -}}
  {{- end -}}
{{- end -}}

{{/*
Render the normalized tag, as modelcar-maker will render it
(called with a model as a map, with repo and optional tag keys)
*/}}
{{- define "modelcar-maker.normalized-tag" -}}
{{- if .tag -}}
  {{- .tag -}}
{{- else -}}
  {{- .repo | replace "/" "--" | replace "." "_" | lower -}}
{{- end -}}
{{- end -}}

{{/*
Render the DNS-normalized name, to support creating specific objects at the
API per-model (e.g. Data Science Connections)
*/}}
{{- define "modelcar-maker.modelcar-dns-normalized-name" -}}
{{- if .tag -}}
  {{- .tag | replace "--" "-" | replace "_" "-" | lower | trunc 63 | trimSuffix "-" -}}
{{- else -}}
  {{- .repo | replace "/" "-" | replace "." "-" | lower | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{/*
Render the destination image tag for a model
(called with a tuple of $model and chart root context)
*/}}
{{- define "modelcar-maker.modelcar-destination-image" -}}
{{- $model := first . -}}
{{- $ctx := last . -}}
{{- $ctx.Values.modelcar.image.registry }}/{{ tpl $ctx.Values.modelcar.image.repository $ctx }}:{{ include "modelcar-maker.normalized-tag" $model -}}
{{- end -}}
