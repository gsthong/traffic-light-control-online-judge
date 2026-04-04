import tempfile, os, subprocess, shutil

tmpdir = tempfile.mkdtemp()
print(f"Temp dir: {tmpdir}")

nod = os.path.join(tmpdir, "nodes.nod.xml")
with open(nod, "w") as f:
    f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
    f.write("<nodes>\n")
    f.write('  <node id="C" x="0.0" y="0.0" type="traffic_light"/>\n')
    f.write('  <node id="N" x="0.0" y="-500.0" type="priority"/>\n')
    f.write('  <node id="S" x="0.0" y="500.0" type="priority"/>\n')
    f.write('  <node id="E" x="500.0" y="0.0" type="priority"/>\n')
    f.write('  <node id="W" x="-500.0" y="0.0" type="priority"/>\n')
    f.write("</nodes>\n")

edg = os.path.join(tmpdir, "edges.edg.xml")
with open(edg, "w") as f:
    f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
    f.write("<edges>\n")
    f.write(
        '  <edge id="N_to_C" from="N" to="C" priority="2" numLanes="1" speed="16.67"/>\n'
    )
    f.write(
        '  <edge id="C_to_N" from="C" to="N" priority="2" numLanes="1" speed="16.67"/>\n'
    )
    f.write(
        '  <edge id="S_to_C" from="S" to="C" priority="2" numLanes="1" speed="16.67"/>\n'
    )
    f.write(
        '  <edge id="C_to_S" from="C" to="S" priority="2" numLanes="1" speed="16.67"/>\n'
    )
    f.write(
        '  <edge id="E_to_C" from="E" to="C" priority="2" numLanes="1" speed="16.67"/>\n'
    )
    f.write(
        '  <edge id="C_to_E" from="C" to="E" priority="2" numLanes="1" speed="16.67"/>\n'
    )
    f.write(
        '  <edge id="W_to_C" from="W" to="C" priority="2" numLanes="1" speed="16.67"/>\n'
    )
    f.write(
        '  <edge id="C_to_W" from="C" to="W" priority="2" numLanes="1" speed="16.67"/>\n'
    )
    f.write("</edges>\n")

tls = os.path.join(tmpdir, "tls.add.xml")
with open(tls, "w") as f:
    f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
    f.write("<additional>\n")
    f.write('  <tlLogic id="C" type="static" programID="0" offset="0">\n')
    f.write('    <phase duration="30" state="GGgg"/>\n')
    f.write('    <phase duration="3" state="yyyy"/>\n')
    f.write('    <phase duration="20" state="ggGG"/>\n')
    f.write('    <phase duration="3" state="yyyy"/>\n')
    f.write("  </tlLogic>\n")
    f.write("</additional>\n")

net = os.path.join(tmpdir, "net.net.xml")
r = subprocess.run(
    [
        "netconvert",
        "--node-files=" + nod,
        "--edge-files=" + edg,
        "--output-file=" + net,
        "--no-internal-links",
    ],
    capture_output=True,
    text=True,
)

print(f"Return code: {r.returncode}")
print(f"STDOUT: {r.stdout}")
print(f"STDERR: {r.stderr[:1000] if r.stderr else 'none'}")

if r.returncode == 0 and os.path.exists(net):
    print(f"Net file size: {os.path.getsize(net)} bytes")
    # Try loading it with sumolib
    import sumolib

    net_obj = sumolib.net.readNet(net)
    print(f"Loaded network with {len(net_obj.getEdges())} edges")
    for edge in net_obj.getEdges():
        print(
            f"  Edge: {edge.getID()} from={edge.getFromNode().getID()} to={edge.getToNode().getID()}"
        )
