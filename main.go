// heicreader reads HEIC and HEIF images with a pure-Go HEVC decoder and hands the
// pixels and the Exif block to whoever asked for them.
//
//	heicreader version
//	heicreader convert IN OUT      OUT ending in .jpg/.jpeg (quality 90) or .png
//	heicreader exif IN OUT         the raw Exif TIFF block, as stored
//	heicreader serve               the protocol the Python package speaks, on stdin/stdout
//
// serve reads one request at a time: a JSON line {"op": "decode"|"exif"|"version",
// "len": N} followed by N bytes of file data. It answers with a JSON line
// {"ok": true|false, "error": "...", "w": W, "h": H, "mode": "RGB"|"RGBA", "len": M}
// followed by M bytes: 8-bit pixels, row by row, for decode, or the Exif block for exif.
// A decode applies the file's clean aperture, rotation and mirroring.
package main

import (
	"bufio"
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"image"
	"image/draw"
	"image/jpeg"
	"image/png"
	"io"
	"os"
	"strings"

	"github.com/gen2brain/h265/heic"
)

// version is set at build time with -ldflags "-X main.version=...".
var version = "dev"

// maxInput bounds one request's file data. A HEIC far larger than this is not a photo.
const maxInput = 1 << 30

func main() {
	if len(os.Args) < 2 {
		usage()
	}
	switch os.Args[1] {
	case "version", "--version":
		fmt.Println(version)
	case "serve":
		if err := serve(os.Stdin, os.Stdout); err != nil {
			fmt.Fprintln(os.Stderr, "heicreader:", err)
			os.Exit(1)
		}
	case "convert":
		if len(os.Args) != 4 {
			usage()
		}
		exitOn(convert(os.Args[2], os.Args[3]))
	case "exif":
		if len(os.Args) != 4 {
			usage()
		}
		exitOn(writeExif(os.Args[2], os.Args[3]))
	default:
		usage()
	}
}

func usage() {
	fmt.Fprintln(os.Stderr, "usage: heicreader version | convert IN OUT | exif IN OUT | serve")
	os.Exit(2)
}

func exitOn(err error) {
	if err != nil {
		fmt.Fprintln(os.Stderr, "heicreader:", err)
		os.Exit(1)
	}
}

// decode reads an image, turning a decoder panic on damaged input into an error.
func decode(data []byte) (img image.Image, err error) {
	defer func() {
		if r := recover(); r != nil {
			img, err = nil, fmt.Errorf("decoder failed: %v", r)
		}
	}()
	return heic.Decode(bytes.NewReader(data), heic.Options{AutoRotate: true})
}

// rawExif returns the stored Exif block, or nil when the file has none.
func rawExif(data []byte) (b []byte, err error) {
	defer func() {
		if r := recover(); r != nil {
			b, err = nil, fmt.Errorf("exif reader failed: %v", r)
		}
	}()
	b, err = heic.RawExif(bytes.NewReader(data))
	if errors.Is(err, heic.ErrNoExif) {
		return nil, nil
	}
	return b, err
}

// pixels returns 8-bit samples row by row: RGB when every pixel is opaque, RGBA otherwise.
func pixels(img image.Image) (string, []byte) {
	b := img.Bounds()
	n, ok := img.(*image.NRGBA)
	if !ok {
		n = image.NewNRGBA(b)
		draw.Draw(n, b, img, b.Min, draw.Src)
	}
	w, h := b.Dx(), b.Dy()
	if !n.Opaque() {
		out := make([]byte, 0, w*h*4)
		for y := 0; y < h; y++ {
			out = append(out, n.Pix[y*n.Stride:y*n.Stride+w*4]...)
		}
		return "RGBA", out
	}
	out := make([]byte, w*h*3)
	o := 0
	for y := 0; y < h; y++ {
		row := n.Pix[y*n.Stride : y*n.Stride+w*4]
		for x := 0; x < len(row); x += 4 {
			out[o], out[o+1], out[o+2] = row[x], row[x+1], row[x+2]
			o += 3
		}
	}
	return "RGB", out
}

type request struct {
	Op  string `json:"op"`
	Len int64  `json:"len"`
}

type response struct {
	OK    bool   `json:"ok"`
	Error string `json:"error,omitempty"`
	W     int    `json:"w,omitempty"`
	H     int    `json:"h,omitempty"`
	Mode  string `json:"mode,omitempty"`
	Len   int    `json:"len"`
}

func serve(stdin io.Reader, stdout io.Writer) error {
	in := bufio.NewReaderSize(stdin, 1<<20)
	out := bufio.NewWriterSize(stdout, 1<<20)
	enc := json.NewEncoder(out)
	for {
		line, err := in.ReadBytes('\n')
		if err == io.EOF && len(bytes.TrimSpace(line)) == 0 {
			return nil
		}
		if err != nil {
			return err
		}
		var req request
		if err := json.Unmarshal(line, &req); err != nil {
			return fmt.Errorf("bad request: %w", err)
		}
		if req.Len < 0 || req.Len > maxInput {
			return fmt.Errorf("request length %d out of range", req.Len)
		}
		data := make([]byte, req.Len)
		if _, err := io.ReadFull(in, data); err != nil {
			return err
		}
		res, body := handle(req.Op, data)
		res.Len = len(body)
		if err := enc.Encode(res); err != nil {
			return err
		}
		if _, err := out.Write(body); err != nil {
			return err
		}
		if err := out.Flush(); err != nil {
			return err
		}
	}
}

func handle(op string, data []byte) (response, []byte) {
	switch op {
	case "version":
		return response{OK: true}, []byte(version)
	case "decode":
		img, err := decode(data)
		if err != nil {
			return response{Error: err.Error()}, nil
		}
		mode, body := pixels(img)
		return response{OK: true, W: img.Bounds().Dx(), H: img.Bounds().Dy(), Mode: mode}, body
	case "exif":
		b, err := rawExif(data)
		if err != nil {
			return response{Error: err.Error()}, nil
		}
		return response{OK: true}, b
	}
	return response{Error: "unknown op " + op}, nil
}

func convert(in, outPath string) error {
	data, err := os.ReadFile(in)
	if err != nil {
		return err
	}
	img, err := decode(data)
	if err != nil {
		return err
	}
	f, err := os.Create(outPath)
	if err != nil {
		return err
	}
	w := bufio.NewWriter(f)
	switch ext := strings.ToLower(outPath[strings.LastIndex(outPath, ".")+1:]); ext {
	case "jpg", "jpeg":
		err = jpeg.Encode(w, img, &jpeg.Options{Quality: 90})
	case "png":
		err = png.Encode(w, img)
	default:
		err = fmt.Errorf("output must end in .jpg, .jpeg or .png, not .%s", ext)
	}
	if err == nil {
		err = w.Flush()
	}
	if cerr := f.Close(); err == nil {
		err = cerr
	}
	return err
}

func writeExif(in, outPath string) error {
	data, err := os.ReadFile(in)
	if err != nil {
		return err
	}
	b, err := rawExif(data)
	if err != nil {
		return err
	}
	if b == nil {
		return errors.New("no Exif block")
	}
	return os.WriteFile(outPath, b, 0o644)
}
