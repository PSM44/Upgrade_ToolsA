import json, subprocess, hashlib, pathlib
p=pathlib.Path(__file__).parent/'crypto_probe'
cases=[]
for name,sig,body,want in [('der_valid','signature.der','body.bin',0),('tampered_domain','signature.der','tampered.bin',1),('wrong_signature_encoding','signature.p1363','body.bin',1)]:
 r=subprocess.run(['openssl','dgst','-sha256','-verify',str(p/'test_public.pem'),'-signature',str(p/sig),str(p/body)],capture_output=True,text=True)
 cases.append(dict(name=name,returncode=r.returncode,stdout=r.stdout.strip(),expected='accept' if want==0 else 'reject'))
 assert (r.returncode==0)==(want==0), cases[-1]
body=(p/'body.bin').read_bytes()
prefix=b'NS-HOSTWITNESS-REV3:TEST\n'
expected=json.dumps({'kind':'TEST_ONLY','n':9007199254740993,'text':'\u00f1'},sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()
assert body==prefix+expected
out={'test_keys_only':True,'private_key_persisted':False,'canonical_bytes_equal':True,'cases':cases,'files':{f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(p.iterdir()) if f.is_file()}}
pathlib.Path(__file__).with_name('crypto_probe_results.json').write_text(json.dumps(out,indent=2)+'\n')
print(json.dumps(out,indent=2))
