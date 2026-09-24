# __PROJECT_NAME__ GitOps Environment

- Environment: __ENVIRONMENT_DISPLAY__
- Argo CD URL: __ARGOCD_URL__
- GitOps repository: __GITOPS_REPOSITORY_URL__
- Target branch: __TARGET_BRANCH__
- Project: __PROJECT_NAME__
- Cluster: __CLUSTER_NAME__
- Argo CD cluster name: __ARGOCD_CLUSTER_NAME__
- Root Application: __ROOT_APPLICATION_NAME__

The root chart manages Helm Applications. The `extras/` chart manages raw manifests placed
under `extras/manifests/<application-name>/`. Keep credentials out of this file.
