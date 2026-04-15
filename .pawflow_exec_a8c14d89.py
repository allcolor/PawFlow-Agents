# Write a tiny HTML file that auto-downloads the content
html = '''<html><body><script>
fetch('https://www.pixazo.ai/models/luma').then(r=>r.text()).then(t=>{
  let d=new DOMParser().parseFromString(t,'text/html');
  // This won't work for SPA - need different approach
});
</script></body></html>'''

# Instead, use Python to read the page directly through the browser's existing session
# Write a JS file that we'll load via file:// protocol
import subprocess, time
DISPLAY = {'DISPLAY': ':99'}

# Write the extraction script
with open('/tmp/extract.js', 'w') as f:
    f.write("""(function(){
  var e=document.querySelector('main')||document.body;
  var b=new Blob([e.innerText],{type:'text/plain'});
  var l=document.createElement('a');
  l.href=URL.createObjectURL(b);
  l.download='luma.txt';
  l.click();
})()""")

# Clear the console line, paste from file using xdotool
# First, select all in console
subprocess.run(['xdotool', 'key', 'ctrl+l'], env=DISPLAY)  # Clear console
time.sleep(0.3)

# Actually, let's try clicking in console first then using a simpler command  
# Use xdotool to click the console input
subprocess.run(['xdotool', 'mousemove', '990', '445', 'click', '1'], env=DISPLAY)
time.sleep(0.5)

# Type very slowly without autocomplete interference - use xdotool key for each char
# Actually, use xsel to set clipboard then paste
import os
os.system('echo -n \'(function(){var e=document.querySelector("main")||document.body;var b=new Blob([e.innerText],{type:"text/plain"});var l=document.createElement("a");l.href=URL.createObjectURL(b);l.download="luma.txt";l.click()})()\' | DISPLAY=:99 xclip -selection clipboard')
time.sleep(0.5)
subprocess.run(['xdotool', 'key', 'ctrl+v'], env=DISPLAY)
time.sleep(0.5)
subprocess.run(['xdotool', 'key', 'Return'], env=DISPLAY)
time.sleep(3)
print('Script pasted and executed')