// Package proxy implements a fasthttp reverse proxy to the Python backend
// with transparent Redis cache interception.
package proxy

import (
	"encoding/json"
	"io"
	"net/http"
	"time"
)

// Backend is a simple HTTP client for the Python Flask API.
type Backend struct {
	BaseURL string
	Client  *http.Client
}

func NewBackend(baseURL string) *Backend {
	return &Backend{
		BaseURL: baseURL,
		Client: &http.Client{
			Timeout: 10 * time.Second,
			Transport: &http.Transport{
				MaxIdleConns:        200,
				MaxIdleConnsPerHost: 200,
				IdleConnTimeout:     90 * time.Second,
			},
		},
	}
}

// Forward sends a request to the Python backend and returns the raw body + status.
func (b *Backend) Forward(method, path string, body []byte, headers map[string]string) ([]byte, int, error) {
	url := b.BaseURL + path

	var bodyReader io.Reader
	if len(body) > 0 {
		bodyReader = io.NopCloser(bytesReader(body))
	}

	req, err := http.NewRequest(method, url, bodyReader)
	if err != nil {
		return nil, 500, err
	}

	req.Header.Set("Content-Type", "application/json")
	for k, v := range headers {
		req.Header.Set(k, v)
	}

	resp, err := b.Client.Do(req)
	if err != nil {
		return nil, 502, err
	}
	defer resp.Body.Close()

	data, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, 502, err
	}

	return data, resp.StatusCode, nil
}

// ForwardJSON sends and parses JSON response.
func (b *Backend) ForwardJSON(method, path string, body []byte, headers map[string]string) (map[string]interface{}, int, error) {
	data, status, err := b.Forward(method, path, body, headers)
	if err != nil {
		return nil, status, err
	}
	var result map[string]interface{}
	if err := json.Unmarshal(data, &result); err != nil {
		return nil, status, err
	}
	return result, status, nil
}

// bytesReader wraps a byte slice as an io.Reader.
type bytesReaderImpl struct {
	data []byte
	pos  int
}

func bytesReader(b []byte) io.Reader {
	return &bytesReaderImpl{data: b}
}

func (r *bytesReaderImpl) Read(p []byte) (int, error) {
	if r.pos >= len(r.data) {
		return 0, io.EOF
	}
	n := copy(p, r.data[r.pos:])
	r.pos += n
	return n, nil
}
