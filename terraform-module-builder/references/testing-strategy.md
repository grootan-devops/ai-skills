# Test Scaffolding

The test levels, what each one covers, and which of them the reference repository actually
runs today are in that repository's `README.md`, §9 *Development & Testing* — read it at the
ref this run resolved. `make verify` is the gate; `tests/verify_modules.py` is what it runs.

This file holds the templates the skill emits.

---

## 1. Native Test — `tests/unit.tftest.hcl`

`mock_provider` needs no credentials and no network, so this runs in seconds and is the
level to add with every new module. `make verify` discovers it automatically: the library's
runner executes `terraform test` in any module directory containing a `*.tftest.hcl`.

Shipping one raises that module's `required_version` to `>= 1.6.0`.

```hcl
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
    error_message = "Bucket name did not match the expected rendered name."
  }

  assert {
    condition     = aws_s3_bucket_server_side_encryption_configuration.this.rule[0].apply_server_side_encryption_by_default[0].kms_master_key_id == var.kms_key_arn
    error_message = "KMS encryption key was not assigned."
  }
}

run "verify_invalid_environment_fails" {
  command = plan

  variables {
    environment = "INVALID_UPPERCASE"
  }

  expect_failures = [var.environment]
}
```

Scaffold both shapes, not just the first. A suite that only asserts the happy path passes
just as well after someone deletes a `validation` block.

## 2. Terratest — `test/<module>_test.go`

For behaviour only a live API reveals. It costs real money and real time, so scaffold it
when the user asks for it, not by default.

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

	defer terraform.Destroy(t, terraformOptions)

	terraform.InitAndApply(t, terraformOptions)

	outputID := terraform.Output(t, terraformOptions, "id")
	assert.NotEmpty(t, outputID)

	// A second plan must be empty, or the module is not idempotent.
	exitCode := terraform.PlanExitCode(t, terraformOptions)
	assert.Equal(t, 0, exitCode, "expected zero changes on the second plan")
}
```

The `TerraformDir` above assumes an `examples/minimal` fixture. The reference repository
ships no `examples/` directory today, so scaffolding this test means scaffolding the fixture
with it.
