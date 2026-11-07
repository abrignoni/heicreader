package main

import (
	"bufio"
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"testing"
)

// roundTrip sends requests through serve and reads the answers back, the way the
// Python package does.
func roundTrip(t *testing.T, reqs ...[2]any) []response {
	t.Helper()
	var in bytes.Buffer
	for _, r := range reqs {
		data := r[1].([]byte)
		fmt.Fprintf(&in, "{\"op\":%q,\"len\":%d}\n", r[0].(string), len(data))
		in.Write(data)
	}
	var out bytes.Buffer
	if err := serve(&in, &out); err != nil {
		t.Fatal(err)
	}
	rd := bufio.NewReader(&out)
	var got []response
	for range reqs {
		line, err := rd.ReadBytes('\n')
		if err != nil {
			t.Fatal(err)
		}
		var res response
		if err := json.Unmarshal(line, &res); err != nil {
			t.Fatal(err)
		}
		if _, err := io.CopyN(io.Discard, rd, int64(res.Len)); err != nil {
			t.Fatal(err)
		}
		got = append(got, res)
	}
	return got
}

func fixture(t *testing.T, name string) []byte {
	t.Helper()
	b, err := os.ReadFile("tests/data/" + name)
	if err != nil {
		t.Fatal(err)
	}
	return b
}

func TestServeAnswersEachRequestInOrder(t *testing.T) {
	plain := fixture(t, "plain.heic")
	got := roundTrip(t,
		[2]any{"decode", plain},
		[2]any{"decode", plain[:100]},
		[2]any{"exif", fixture(t, "orientation.heic")},
		[2]any{"decode", fixture(t, "grid_alpha.heic")},
		[2]any{"nope", []byte{}},
	)
	want := []struct {
		ok   bool
		w, h int
		mode string
	}{
		{true, 200, 120, "RGB"},
		{false, 0, 0, ""},
		{true, 0, 0, ""},
		{true, 1100, 700, "RGBA"},
		{false, 0, 0, ""},
	}
	for i, w := range want {
		g := got[i]
		if g.OK != w.ok || g.W != w.w || g.H != w.h || g.Mode != w.mode {
			t.Errorf("request %d: got %+v, want %+v", i, g, w)
		}
	}
	if got[0].Len != 200*120*3 || got[3].Len != 1100*700*4 {
		t.Errorf("pixel lengths %d and %d", got[0].Len, got[3].Len)
	}
	if got[2].Len == 0 {
		t.Error("orientation.heic: no Exif returned")
	}
}

func TestNoExifIsAnEmptyAnswer(t *testing.T) {
	got := roundTrip(t, [2]any{"exif", fixture(t, "plain.heic")})
	if !got[0].OK || got[0].Len != 0 {
		t.Errorf("got %+v, want ok with no data", got[0])
	}
}

func TestAnOversizedRequestStopsTheServer(t *testing.T) {
	in := bytes.NewBufferString(fmt.Sprintf("{\"op\":\"decode\",\"len\":%d}\n", int64(maxInput)+1))
	if err := serve(in, io.Discard); err == nil {
		t.Error("an oversized request was accepted")
	}
}
