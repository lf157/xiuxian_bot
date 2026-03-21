// Package handler implements the fasthttp request handlers with Redis cache.
package handler

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"strings"
	"time"

	"github.com/valyala/fasthttp"

	"xiuxian-gateway/internal/cache"
	"xiuxian-gateway/internal/proxy"
)

const (
	playerCacheTTL = 60 * time.Second
	internalToken  = "" // set from config
)

// Router holds dependencies.
type Router struct {
	Store   *cache.Store
	Backend *proxy.Backend
	Token   string // X-Internal-Token for backend
}

// Handle is the main fasthttp handler.
func (r *Router) Handle(ctx *fasthttp.RequestCtx) {
	path := string(ctx.Path())
	method := string(ctx.Method())

	// ── Cached read paths ──
	if method == "GET" {
		switch {
		case strings.HasPrefix(path, "/api/stat/"):
			r.handleStat(ctx, path)
			return
		case strings.HasPrefix(path, "/api/story/volumes/"):
			r.handleStoryVolumes(ctx, path)
			return
		case strings.HasPrefix(path, "/api/cultivate/status/"):
			r.handleCultivateStatus(ctx, path)
			return
		}
	}

	// ── Cache-aware write paths ──
	if method == "POST" {
		switch {
		case path == "/api/cultivate/start":
			r.handleCultivateStart(ctx)
			return
		case path == "/api/cultivate/end":
			r.handleCultivateEnd(ctx)
			return
		case path == "/api/story/read":
			r.handleStoryRead(ctx)
			return
		case path == "/api/signin":
			r.handleSignin(ctx)
			return
		case path == "/api/breakthrough":
			r.handleBreakthrough(ctx)
			return
		}
	}

	// ── Everything else: pass through ──
	r.passThrough(ctx)
}

// ── Stat (GET) — cache in Redis Hash ────────────────────

func (r *Router) handleStat(ctx *fasthttp.RequestCtx, path string) {
	uid := strings.TrimPrefix(path, "/api/stat/")
	if uid == "" {
		jsonError(ctx, 400, "missing uid")
		return
	}

	bg := context.Background()

	// Try cache first
	fields, err := r.Store.GetPlayer(bg, uid)
	if err == nil && len(fields) > 0 {
		// Cache hit → build response from Hash
		resp := buildStatFromHash(fields)
		jsonOK(ctx, resp)
		return
	}

	// Cache miss → forward to Python
	body, status, err := r.Backend.Forward("GET", path, nil, r.backendHeaders(ctx))
	if err != nil {
		jsonError(ctx, 502, "backend error")
		return
	}

	// Parse and cache
	var parsed map[string]interface{}
	if status == 200 {
		if err := json.Unmarshal(body, &parsed); err == nil {
			if statusData, ok := parsed["status"].(map[string]interface{}); ok {
				cacheFields := cache.MapToPlayerFields(statusData)
				_ = r.Store.SetPlayer(bg, uid, cacheFields, playerCacheTTL)
			}
		}
	}

	ctx.SetStatusCode(status)
	ctx.SetContentType("application/json")
	ctx.SetBody(body)
}

// ── Cultivate status (GET) — read from Redis Hash ───────

func (r *Router) handleCultivateStatus(ctx *fasthttp.RequestCtx, path string) {
	uid := strings.TrimPrefix(path, "/api/cultivate/status/")
	bg := context.Background()

	cult, err := r.Store.GetCultivation(bg, uid)
	if err == nil && len(cult) > 0 {
		jsonOK(ctx, map[string]interface{}{
			"cultivating":  true,
			"start_time":   cult["start"],
			"gain_per_hour": cult["gain"],
			"boost_info":   cult["boost"],
		})
		return
	}

	// Cache miss → forward
	r.forwardAndReturn(ctx, "GET", path, nil)
}

// ── Cultivate start (POST) — cache session in Hash ──────

func (r *Router) handleCultivateStart(ctx *fasthttp.RequestCtx) {
	body, status, err := r.Backend.Forward("POST", "/api/cultivate/start", ctx.PostBody(), r.backendHeaders(ctx))
	if err != nil {
		jsonError(ctx, 502, "backend error")
		return
	}

	if status == 200 {
		var parsed map[string]interface{}
		if json.Unmarshal(body, &parsed) == nil {
			uid := extractUID(ctx.PostBody())
			if uid != "" {
				startTime, _ := toInt64(parsed["start_time"])
				gainPerHour, _ := toInt(parsed["gain_per_hour"])
				boostInfo := fmt.Sprint(parsed["sprint_boost_mult"])
				_ = r.Store.SetCultivation(context.Background(), uid, startTime, gainPerHour, boostInfo)
			}
		}
	}

	ctx.SetStatusCode(status)
	ctx.SetContentType("application/json")
	ctx.SetBody(body)
}

// ── Cultivate end (POST) — clear cache, invalidate player ─

func (r *Router) handleCultivateEnd(ctx *fasthttp.RequestCtx) {
	body, status, err := r.Backend.Forward("POST", "/api/cultivate/end", ctx.PostBody(), r.backendHeaders(ctx))
	if err != nil {
		jsonError(ctx, 502, "backend error")
		return
	}

	bg := context.Background()
	uid := extractUID(ctx.PostBody())
	if uid != "" {
		_ = r.Store.DelCultivation(bg, uid)
		_ = r.Store.DelPlayer(bg, uid) // Invalidate player cache (exp changed)
	}

	ctx.SetStatusCode(status)
	ctx.SetContentType("application/json")
	ctx.SetBody(body)
}

// ── Story read (POST) — cache line position in Hash ─────

func (r *Router) handleStoryRead(ctx *fasthttp.RequestCtx) {
	body, status, err := r.Backend.Forward("POST", "/api/story/read", ctx.PostBody(), r.backendHeaders(ctx))
	if err != nil {
		jsonError(ctx, 502, "backend error")
		return
	}

	if status == 200 {
		var parsed map[string]interface{}
		if json.Unmarshal(body, &parsed) == nil {
			uid := extractUID(ctx.PostBody())
			chapterID, _ := parsed["chapter_id"].(string)
			currentLine, _ := toInt(parsed["current_line"])
			if uid != "" && chapterID != "" {
				_ = r.Store.SetStoryLine(context.Background(), uid, chapterID, currentLine)
			}
		}
	}

	ctx.SetStatusCode(status)
	ctx.SetContentType("application/json")
	ctx.SetBody(body)
}

// ── Story volumes (GET) — pass through (low frequency) ──

func (r *Router) handleStoryVolumes(ctx *fasthttp.RequestCtx, path string) {
	r.forwardAndReturn(ctx, "GET", path, nil)
}

// ── Signin (POST) — set bitmap + invalidate player ──────

func (r *Router) handleSignin(ctx *fasthttp.RequestCtx) {
	body, status, err := r.Backend.Forward("POST", "/api/signin", ctx.PostBody(), r.backendHeaders(ctx))
	if err != nil {
		jsonError(ctx, 502, "backend error")
		return
	}

	if status == 200 {
		uid := extractUID(ctx.PostBody())
		if uid != "" {
			bg := context.Background()
			now := time.Now()
			month := now.Format("200601")
			day := now.Day()
			_ = r.Store.SetSignin(bg, uid, month, day)
			_ = r.Store.DelPlayer(bg, uid) // Invalidate (exp/copper changed)
		}
	}

	ctx.SetStatusCode(status)
	ctx.SetContentType("application/json")
	ctx.SetBody(body)
}

// ── Breakthrough (POST) — invalidate player ─────────────

func (r *Router) handleBreakthrough(ctx *fasthttp.RequestCtx) {
	body, status, err := r.Backend.Forward("POST", "/api/breakthrough", ctx.PostBody(), r.backendHeaders(ctx))
	if err != nil {
		jsonError(ctx, 502, "backend error")
		return
	}

	if status == 200 {
		uid := extractUID(ctx.PostBody())
		if uid != "" {
			_ = r.Store.DelPlayer(context.Background(), uid) // Rank/exp/stats all changed
		}
	}

	ctx.SetStatusCode(status)
	ctx.SetContentType("application/json")
	ctx.SetBody(body)
}

// ── Pass-through ────────────────────────────────────────

func (r *Router) passThrough(ctx *fasthttp.RequestCtx) {
	method := string(ctx.Method())
	path := string(ctx.Path())
	var reqBody []byte
	if method == "POST" || method == "PUT" || method == "PATCH" {
		reqBody = ctx.PostBody()
	}
	r.forwardAndReturn(ctx, method, path, reqBody)
}

func (r *Router) forwardAndReturn(ctx *fasthttp.RequestCtx, method, path string, body []byte) {
	respBody, status, err := r.Backend.Forward(method, path, body, r.backendHeaders(ctx))
	if err != nil {
		jsonError(ctx, 502, "backend error")
		return
	}
	ctx.SetStatusCode(status)
	ctx.SetContentType("application/json")
	ctx.SetBody(respBody)
}

func (r *Router) backendHeaders(ctx *fasthttp.RequestCtx) map[string]string {
	h := map[string]string{}
	if r.Token != "" {
		h["X-Internal-Token"] = r.Token
	}
	// Forward actor header
	actor := string(ctx.Request.Header.Peek("X-Actor-User-Id"))
	if actor != "" {
		h["X-Actor-User-Id"] = actor
	}
	return h
}

// ── Helpers ─────────────────────────────────────────────

func jsonOK(ctx *fasthttp.RequestCtx, data interface{}) {
	resp := map[string]interface{}{"success": true, "status": data}
	b, _ := json.Marshal(resp)
	ctx.SetStatusCode(200)
	ctx.SetContentType("application/json")
	ctx.SetBody(b)
}

func jsonError(ctx *fasthttp.RequestCtx, code int, msg string) {
	resp := map[string]interface{}{"success": false, "message": msg}
	b, _ := json.Marshal(resp)
	ctx.SetStatusCode(code)
	ctx.SetContentType("application/json")
	ctx.SetBody(b)
}

func buildStatFromHash(fields map[string]string) map[string]interface{} {
	result := make(map[string]interface{}, len(fields))
	for k, v := range fields {
		result[k] = v
	}
	return result
}

func extractUID(body []byte) string {
	var m map[string]interface{}
	if json.Unmarshal(body, &m) != nil {
		return ""
	}
	uid, _ := m["user_id"].(string)
	return uid
}

func toInt64(v interface{}) (int64, bool) {
	switch n := v.(type) {
	case float64:
		return int64(n), true
	case int64:
		return n, true
	case json.Number:
		i, err := n.Int64()
		return i, err == nil
	}
	return 0, false
}

func toInt(v interface{}) (int, bool) {
	n, ok := toInt64(v)
	return int(n), ok
}

func init() {
	log.SetFlags(log.Ldate | log.Ltime | log.Lshortfile)
}
