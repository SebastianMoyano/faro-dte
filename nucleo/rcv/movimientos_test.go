package rcv

import (
	"reflect"
	"testing"
	"time"
)

func TestPeriodosDeMovimientos(t *testing.T) {
	if g := PeriodosPorDefecto(time.Date(2026, 10, 31, 9, 0, 0, 0, time.Local)); !reflect.DeepEqual(g, []string{"202610", "202609"}) {
		t.Fatalf("31-oct: %v", g)
	}
	if g := PeriodosPorDefecto(time.Date(2026, 1, 5, 9, 0, 0, 0, time.Local)); !reflect.DeepEqual(g, []string{"202601", "202512"}) {
		t.Fatalf("enero: %v", g)
	}
	if g := ParsearPeriodos("202610, 2026-09,202610"); !reflect.DeepEqual(g, []string{"202610", "202609"}) {
		t.Fatalf("lista: %v", g)
	}
	if len(ParsearPeriodos("")) != 2 {
		t.Fatal("vacío → dos meses")
	}
	for _, malo := range []string{"202613", "2026", "abcdef", "202600", "202601,202602,202603,202604"} {
		if ParsearPeriodos(malo) != nil {
			t.Errorf("debió rechazar %q", malo)
		}
	}
}
