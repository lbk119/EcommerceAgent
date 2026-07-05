package authorization

import (
	"fmt"

	"github.com/casbin/casbin/v2"
)

// NewEnforcer 从文件加载 Casbin model 和 policy。
// 当前阶段使用 file adapter，便于本地调试和代码审查；生产环境如果需要把策略放到 MySQL，
// 可以在这里替换为 gorm-adapter，而路由、中间件和 Python Brain 都不需要感知存储变化。
func NewEnforcer(modelPath string, policyPath string) (*casbin.Enforcer, error) {
	enforcer, err := casbin.NewEnforcer(modelPath, policyPath)
	if err != nil {
		return nil, fmt.Errorf("load casbin enforcer: %w", err)
	}

	// 显式加载策略，让启动阶段尽早暴露 model/policy 路径错误或 CSV 格式错误。
	if err := enforcer.LoadPolicy(); err != nil {
		return nil, fmt.Errorf("load casbin policy: %w", err)
	}
	return enforcer, nil
}
