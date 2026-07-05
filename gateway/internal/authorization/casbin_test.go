package authorization

import "testing"

func TestCasbinRBACWithDomains(t *testing.T) {
	enforcer, err := NewEnforcer("../../configs/casbin/model.conf", "../../configs/casbin/policy.csv")
	if err != nil {
		t.Fatalf("load enforcer: %v", err)
	}

	tests := []struct {
		name     string
		sub      string
		dom      string
		obj      string
		act      string
		expected bool
	}{
		{
			name:     "admin can create task in tenant",
			sub:      "local_user",
			dom:      "tenant_demo",
			obj:      "/api/v1/tasks",
			act:      "POST",
			expected: true,
		},
		{
			name:     "admin route pattern matches task id",
			sub:      "local_user",
			dom:      "tenant_demo",
			obj:      "/api/v1/tasks/abc-123/cancel",
			act:      "POST",
			expected: true,
		},
		{
			name:     "gin full path pattern matches policy path pattern",
			sub:      "local_user",
			dom:      "tenant_demo",
			obj:      "/api/v1/tasks/:thread_id/cancel",
			act:      "POST",
			expected: true,
		},
		{
			name:     "tenant isolation denies same user in other tenant",
			sub:      "local_user",
			dom:      "other_tenant",
			obj:      "/api/v1/tasks",
			act:      "POST",
			expected: false,
		},
		{
			name:     "viewer cannot create task",
			sub:      "viewer_user",
			dom:      "tenant_demo",
			obj:      "/api/v1/tasks",
			act:      "POST",
			expected: false,
		},
	}

	if _, err := enforcer.AddRoleForUserInDomain("viewer_user", "viewer", "tenant_demo"); err != nil {
		t.Fatalf("add viewer role: %v", err)
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			allowed, err := enforcer.Enforce(test.sub, test.dom, test.obj, test.act)
			if err != nil {
				t.Fatalf("enforce: %v", err)
			}
			if allowed != test.expected {
				t.Fatalf("expected %v, got %v", test.expected, allowed)
			}
		})
	}
}
