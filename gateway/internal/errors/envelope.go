package errors

import (
	"net/http"

	"github.com/gin-gonic/gin"
)

const requestIDHeader = "X-Request-ID"

// Envelope is used only for gateway-generated errors in this phase.
// Python Brain responses are intentionally not wrapped yet to avoid frontend churn.
type Envelope struct {
	Success   bool        `json:"success"`
	Error     ErrorDetail `json:"error"`
	RequestID string      `json:"request_id"`
}

type ErrorDetail struct {
	Code    string      `json:"code"`
	Message string      `json:"message"`
	Details interface{} `json:"details"`
}

func Abort(c *gin.Context, statusCode int, code string, message string, details interface{}) {
	requestID, _ := c.Get(requestIDHeader)
	if requestID == nil {
		requestID = c.GetHeader(requestIDHeader)
	}
	c.AbortWithStatusJSON(statusCode, Envelope{
		Success: false,
		Error: ErrorDetail{
			Code:    code,
			Message: message,
			Details: details,
		},
		RequestID: requestIDString(requestID),
	})
}

func Unauthorized(c *gin.Context, code string, message string) {
	Abort(c, http.StatusUnauthorized, code, message, nil)
}

func Forbidden(c *gin.Context, code string, message string) {
	Abort(c, http.StatusForbidden, code, message, nil)
}

func requestIDString(value interface{}) string {
	if text, ok := value.(string); ok {
		return text
	}
	return ""
}
