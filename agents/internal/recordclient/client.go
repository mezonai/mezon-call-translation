// Package recordclient is the gRPC client for forwarding decoded PCM audio
// to record-service. Ported from the old Python agent's
// (Architect_MultiClient_Server/agents/src/services/record_service_client.py)
// -- see audio-ingestion/PLAN.md D3: the agent is the only component that
// talks to the SFU, so it forwards already-decoded frames (it needs them
// for STT anyway) rather than record-service joining the room itself.
//
// Forwarding is strictly best-effort and non-blocking from the caller's
// point of view (PLAN.md D5: "tuyệt đối không để việc này chặn pipeline
// STT"). If record-service is slow or unreachable, frames are dropped (and
// the drop count reported, PLAN.md D12), never awaited-on by the audio hot
// path -- see Forwarder.SendPCM.
package recordclient

import (
	"fmt"

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"

	"github.com/mezonai/mezon-call-translation/agents/internal/recordpb"
)

// Client is a process-wide gRPC channel/stub. Cheap to keep open -- one per
// agent process, shared across every track's Forwarder.
type Client struct {
	conn *grpc.ClientConn
	stub recordpb.RecordingIngestClient
}

func Dial(addr string) (*Client, error) {
	conn, err := grpc.NewClient(addr, grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		return nil, fmt.Errorf("recordclient: dial %s: %w", addr, err)
	}
	return &Client{conn: conn, stub: recordpb.NewRecordingIngestClient(conn)}, nil
}

func (c *Client) Close() error {
	return c.conn.Close()
}
