import subprocess, sys, time, os
script = os.path.abspath('webview_launcher.py')
print('Launching:', script)
proc = subprocess.Popen(
    [sys.executable, script, 'http://localhost:4300'],
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
)
time.sleep(4)
rc = proc.poll()
if rc is None:
    print('Process still running after 4s - window opened OK - killing')
    proc.kill()
    proc.wait()
else:
    out, err = proc.communicate()
    print('Process exited with code:', rc)
    print('STDOUT:', out.decode('utf-8', errors='replace'))
    print('STDERR:', err.decode('utf-8', errors='replace'))
