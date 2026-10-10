package langfuse

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"strconv"
)

// Dataset is a Langfuse dataset with its items, as an experiment runner reads it.
type Dataset struct {
	ID    string
	Name  string
	Items []DatasetItem
}

// DatasetItem is one item. Its input, expected output and metadata are what the dataset holds: content.
type DatasetItem struct {
	ID             string          `json:"id"`
	Status         string          `json:"status"` // ACTIVE or ARCHIVED: a runner skips archived items
	Input          json.RawMessage `json:"input"`
	ExpectedOutput json.RawMessage `json:"expectedOutput"`
	Metadata       json.RawMessage `json:"metadata"`
}

// Dataset reads a dataset and all its items (GET /api/public/v2/datasets/{name}, then
// GET /api/public/dataset-items page by page, as the SDKs do); version, an ISO 8601 time or "", pins the items as
// they were then. For an experiment runner, never a request path.
func (c *Client) Dataset(ctx context.Context, name, version string) (Dataset, error) {
	var head struct {
		ID   string `json:"id"`
		Name string `json:"name"`
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, c.base+"/api/public/v2/datasets/"+url.PathEscape(name), nil)
	if err != nil {
		return Dataset{}, err
	}
	if err := c.do(req, &head); err != nil {
		return Dataset{}, err
	}
	dataset := Dataset{ID: head.ID, Name: head.Name}
	for page := 1; page <= 1000; page++ {
		query := url.Values{"datasetName": {name}, "page": {strconv.Itoa(page)}, "limit": {"50"}}
		if version != "" {
			query.Set("version", version)
		}
		req, err := http.NewRequestWithContext(ctx, http.MethodGet, c.base+"/api/public/dataset-items?"+query.Encode(), nil)
		if err != nil {
			return Dataset{}, err
		}
		var body struct {
			Data []DatasetItem `json:"data"`
			Meta struct {
				TotalPages int `json:"totalPages"`
			} `json:"meta"`
		}
		if err := c.do(req, &body); err != nil {
			return Dataset{}, err
		}
		dataset.Items = append(dataset.Items, body.Data...)
		if page >= body.Meta.TotalPages || len(body.Data) == 0 {
			return dataset, nil
		}
	}
	return Dataset{}, fmt.Errorf("langfuse dataset %s: more than 1000 pages", name)
}
