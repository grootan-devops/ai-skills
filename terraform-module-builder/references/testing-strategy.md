# Testing Strategy & 7-Level Pyramid Specification

This document details the layered testing strategy enforced by the `terraform-module-builder` skill, combining native Terraform 1.6+ tests, mock providers, static security scanning, and Go-based Terratest suites.

---

## 1. The 7-Level Testing Pyramid

```text
                      ┌────────────────────────┐
                      │ Level 6: Compatibility │  (Terraform core & provider version matrix)
                      ├────────────────────────┤
                      │ Level 5: Upgrade Tests │  (State migration & moved block validation)
                      ├────────────────────────┤
                      │ Level 4: Terratest     │  (Real-cloud apply, idempotency, destroy)
                      ├────────────────────────┤
                      │ Level 3: Plan Tests    │  (Feature combinations & destructive change detection)
                      ├────────────────────────┤
                      │ Level 2: Native Tests  │  (terraform test with mock providers)
                      ├────────────────────────┤
                      │ Level 1: Static Sec    │  (Trivy IaC scan, secret & identity scanner)
                      ├────────────────────────┤
                      │ Level 0: Source Quality│  (terraform fmt, validate, TFLint, ShellCheck)
                      └────────────────────────┘
```

### 1.1. Level 0: Source Quality & Formatting Gate

- `terraform fmt -check -recursive`: Must return exit code 0.
- `terraform validate`: Validates HCL syntax and internal references.
- `tflint --recursive`: Catches provider-specific deprecations and anti-patterns.
- `shellcheck scripts/*.sh`: Validates all auxiliary shell scripts.

### 1.2. Level 1: Static Security & Secret Gate

- `trivy config --exit-code 1 --severity HIGH,CRITICAL .`: Blocks misconfigurations (e.g. unencrypted storage, public ingress).
- **Secret Scanner**: Ensures zero cleartext credentials, API keys, or fake passwords exist in `variables.tf` or code.
- **Identity Scanner**: Ensures zero hardcoded account IDs, subscription IDs, or tenant IDs exist.

### 1.3. Level 2: Native Terraform Tests (`terraform test` with Mock Providers)

Located in `tests/*.tftest.hcl`. Runs instantaneously without cloud credentials using Terraform 1.6+ mock providers.

```hcl
# tests/unit.tftest.hcl

mock_provider "aws" {}

variables {
  application = "core"
  environment = "prod"
  name        = "test-resource"
  kms_key_arn = "arn:aws:kms:us-west-2:123456789012:key/12345678-1234-1234-1234-123456789012"
}

run "verify_security_defaults" {
  command = plan

  assert {
    condition     = aws_s3_bucket.this.bucket == "core-prod-test-resource"
    error_message = "Bucket name did not match expected rendered name."
  }

  assert {
    condition     = aws_s3_bucket_server_side_encryption_configuration.this.rule[0].apply_server_side_encryption_by_default[0].kms_master_key_id == var.kms_key_arn
    error_message = "KMS encryption key was not correctly assigned."
  }
}

run "verify_invalid_environment_fails" {
  command = plan

  variables {
    environment = "INVALID_UPPERCASE"
  }

  expect_failures = [
    var.environment
  ]
}
```

### 1.4. Level 3: Plan Analysis & Destructive Change Detection

- Compares plans between `examples/minimal` and `examples/complete`.
- Analyzes plan JSON (`terraform show -json tfplan`) to ensure zero unexpected `delete` actions on stateful resources during minor/patch upgrades.

### 1.5. Level 4: Real Cloud Infrastructure Acceptance (Terratest)

Used for deep integration tests where live provider API interactions are required. Located in `test/<module>_test.go`:

```go
package test

import (
 "testing"
 "github.com/gruntwork-io/terratest/modules/terraform"
 "github.com/stretchr/testify/assert"
)

func TestModuleLifecycle(t *testing.T) {
 t.Parallel()

 terraformOptions := terraform.WithDefaultRetryableErrors(t, &terraform.Options{
  TerraformDir: "../examples/minimal",
  Vars: map[string]interface{}{
   "application": "testapp",
   "environment": "dev",
  },
 })

 // Ensure clean destruction at end of test run
 defer terraform.Destroy(t, terraformOptions)

 // Step 1: Initial Apply
 terraform.InitAndApply(t, terraformOptions)

 // Step 2: Validate Outputs
 outputID := terraform.Output(t, terraformOptions, "id")
 assert.NotEmpty(t, outputID)

 // Step 3: Idempotency Check (Second plan must have zero changes)
 exitCode := terraform.PlanExitCode(t, terraformOptions)
 assert.Equal(t, 0, exitCode, "Expected zero changes on subsequent plan (drift/idempotency failure)")
}
```

### 1.6. Level 5: Upgrade & State Migration Tests

- Provisions baseline version `v(N-1)` of the module.
- Upgrades source reference to `v(N)` containing `moved` blocks.
- Executes `terraform plan -detailed-exitcode` asserting **zero recreations/destroys**.

### 1.7. Level 6: Version Compatibility Matrix

Executes test suites across a compatibility matrix:

- Minimum supported Terraform core version (e.g. `1.5.8` or `1.11.0`) vs Latest stable (`1.16+`).
- Minimum supported provider version vs Latest stable provider version.
