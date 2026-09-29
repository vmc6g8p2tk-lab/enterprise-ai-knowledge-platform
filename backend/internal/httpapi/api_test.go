package httpapi

import (
	"net/http/httptest"
	"strings"
	"testing"
)

func TestUUIDValidation(t *testing.T) {
	if !validUUID("8e6b2de8-079d-4f6d-bcb7-73068cc05a40") { t.Fatal("valid UUID rejected") }
	for _, input := range []string{"", "../secret", "8e6b2de8-079d-4f6d-bcb7-73068cc05a4Z"} {
		if validUUID(input) { t.Fatalf("invalid UUID accepted: %q", input) }
	}
}

func TestDecodeJSONRejectsExtraData(t *testing.T) {
	request := httptest.NewRequest("POST", "/", strings.NewReader(`{"name":"one"}{"name":"two"}`))
	response := httptest.NewRecorder()
	var value struct { Name string `json:"name"` }
	if err := decodeJSON(response, request, &value); err == nil { t.Fatal("accepted two JSON documents") }
}

func TestNewUUID(t *testing.T) {
	a,err := newUUID(); if err != nil { t.Fatal(err) }
	b,err := newUUID(); if err != nil { t.Fatal(err) }
	if !validUUID(a) || a==b { t.Fatalf("invalid or duplicate UUID: %q %q",a,b) }
}
