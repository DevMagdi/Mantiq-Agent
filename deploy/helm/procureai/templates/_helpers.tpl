{{- define "procureai.labels" -}}
app.kubernetes.io/name: procureai
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: procureai-platform
{{- end -}}

{{- define "procureai.image" -}}
{{ .Values.image.registry }}/{{ .component }}:{{ .Values.image.tag }}
{{- end -}}
