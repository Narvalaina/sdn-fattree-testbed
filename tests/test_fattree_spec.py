from tfg_sdn.mininet.topos.fattree import build_fattree_spec

def test_k4_manifest_and_counts():
    spec=build_fattree_spec(4)
    m=spec["manifest"]
    assert m["core_switches"]==4
    assert m["aggregation_switches"]==8
    assert m["edge_switches"]==8
    assert m["hosts"]==16
    assert m["switches_total"]==20
    assert m["links_total"]==48
    assert len(spec["nodes"])==36
    assert len(spec["links"])==48
    assert len(spec["hostmap"])==16

def test_fattree_requires_even_k_at_least_four():
    for k in (1,2,3,5):
        try:
            build_fattree_spec(k)
        except ValueError:
            pass
        else:
            raise AssertionError(f"k={k} should be rejected")
