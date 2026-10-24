// wire-pod plugin: sends "Hey Vector, Tuesday …" voice commands to Tuesday and has Vector speak the reply.
//
// Build on the machine running wire-pod (see wire-pod wiki "For Developers and Tinkerers"):
//   sudo /usr/local/go/bin/go build -buildmode=plugin -ldflags "-w -s" -o ~/wire-pod/chipper/plugins/tuesday.so tuesday_plugin.go
//   then restart wire-pod.
// If wire-pod runs on a different machine than Tuesday, change tuesdayURL to this Mac's LAN address and start
// Tuesday with TUESDAY_HOST=0.0.0.0.
package main

import (
	"bytes"
	"encoding/json"
	"net/http"
	"strings"
	"time"
)

var Utterances = []string{"tuesday"}
var Name = "Tuesday"

// For a cloud brain: "https://tuesday.example.com/chat" and set tuesdayToken to TUESDAY_API_TOKEN.
const tuesdayURL = "http://127.0.0.1:4343/chat"
const tuesdayToken = ""

func Action(transcribedText string, botSerial string, guid string, target string) (string, string) {
	text := strings.TrimSpace(transcribedText)
	if i := strings.Index(strings.ToLower(text), "tuesday"); i >= 0 {
		text = strings.TrimSpace(text[i+len("tuesday"):])
	}
	if text == "" {
		text = "Hello"
	}
	body, _ := json.Marshal(map[string]string{"message": text, "channel": "wirepod"})
	client := http.Client{Timeout: 60 * time.Second}
	req, _ := http.NewRequest("POST", tuesdayURL, bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	if tuesdayToken != "" {
		req.Header.Set("Authorization", "Bearer "+tuesdayToken)
	}
	resp, err := client.Do(req)
	if err != nil {
		return "", "I can't reach Tuesday right now."
	}
	defer resp.Body.Close()
	var out struct {
		Reply string `json:"reply"`
	}
	if json.NewDecoder(resp.Body).Decode(&out) != nil || out.Reply == "" {
		return "", "Tuesday had nothing to say."
	}
	return "", out.Reply
}
