const SOURCE = [130, 0, 930, 1360];
const LOOP_SECONDS = 12;
const MAX_PIXEL_RATIO = 2;
const BODY_GRID = [70, 88];
const BODY = 0;
const BEAM = 2;
const LEFT_PAN = 3;
const RIGHT_PAN = 4;
const ORIGIN = `vec2(${SOURCE[0]}.,${SOURCE[1]}.)`;
const SIZE = `vec2(${SOURCE[2]}.,${SOURCE[3]}.)`;
const PARTS = [
  [BODY, SOURCE[0], SOURCE[1], SOURCE[0] + SOURCE[2], SOURCE[1] + SOURCE[3]],
  [1, 814, 534, 861, 580],
  [BEAM, 710, 580, 960, 618],
  [LEFT_PAN, 667, 618, 804, 814],
  [RIGHT_PAN, 871, 618, 1009, 814],
  [BEAM, 814, 618, 861, 643],
];
const reduce = matchMedia("(prefers-reduced-motion: reduce)");
const running = new Map();

const VERTEX = `attribute vec2 aPos;uniform mediump float uTime;uniform mediump int uPart;varying vec2 vUV;
const float PI=3.14159265359;
vec2 turn(vec2 p,vec2 c,float a){float s=sin(a),co=cos(a);p-=c;return c+vec2(co*p.x-s*p.y,s*p.x+co*p.y);}
void main(){vec2 p=aPos;vUV=(aPos-${ORIGIN})/${SIZE};float q=uTime*2.*PI/${LOOP_SECONDS}.;float breath=sin(3.*q);float sway=sin(q);float envelope=.55+.45*pow(sin(q*.5),2.);float beam=.095*envelope*sin(3.*q);vec2 hub=vec2(837.,602.);
if(uPart==${BODY}){
float torso=exp(-pow((p.x-535.)/145.,4.)-pow((p.y-365.)/170.,4.));
p.x+=(p.x-535.)*.0025*breath*torso;p.y-=2.8*breath*torso;
float upper=exp(-pow((p.x-545.)/145.,4.))*(1.-smoothstep(330.,1180.,p.y));
p.x+=5.*sway*upper;
float head=exp(-pow((p.x-550.)/110.,4.)-pow((p.y-135.)/130.,4.));
p=turn(p,vec2(550.,235.),.022*(1.-cos(q))*head);
float cloth=exp(-pow((p.x-560.)/185.,6.))*smoothstep(520.,1000.,p.y)*(1.-smoothstep(1220.,1310.,p.y));
p.x+=8.*sin(2.*q-(p.y-550.)*.003)*cloth;p.y+=.65*sin(2.*q)*cloth;
float hair=exp(-pow((p.y-208.)/70.,4.))*(exp(-pow((p.x-474.)/25.,2.))+exp(-pow((p.x-614.)/22.,2.)));
p.x+=1.2*sin(2.*q-.6)*hair;
}else if(uPart==${BEAM}){p=turn(p,hub,beam);}else if(uPart==${LEFT_PAN}||uPart==${RIGHT_PAN}){
vec2 anchor=uPart==${LEFT_PAN}?vec2(735.,618.):vec2(939.,618.);
float swing=.10*envelope*sin(4.*q+(uPart==${LEFT_PAN}?0.:.55));
p=turn(p,anchor,swing);p+=turn(anchor,hub,beam)-anchor;
}
vec2 c=(p-${ORIGIN})/${SIZE};gl_Position=vec4(c.x*2.-1.,1.-c.y*2.,0.,1.);}`;

const FRAGMENT = `precision mediump float;uniform sampler2D uImage;uniform float uTime;uniform int uPart;varying vec2 vUV;
void main(){vec4 col=texture2D(uImage,vUV);vec2 p=${ORIGIN}+vUV*${SIZE};
if(uPart==${BODY}){if(p.x>667.&&p.x<1009.&&p.y>534.&&p.y<814.)col=vec4(0.);
float scan=590.+800.*fract(uTime/${LOOP_SECONDS / 2}.);
float shine=exp(-pow((p.y-scan)/52.,2.));
float taper=clamp((1276.-p.y)/112.,0.,1.);
float left=mix(279.,257.,taper),right=mix(279.,302.,taper);
float blade=smoothstep(left,left+2.,p.x)*(1.-smoothstep(right-2.,right,p.x))*smoothstep(678.,697.,p.y)*(1.-smoothstep(1265.,1276.,p.y));
col.rgb+=(col.a-col.rgb)*vec3(.40,.78,1.)*shine*blade*.85;}
gl_FragColor=col;}`;

function compile(gl, type, source) {
  const shader = gl.createShader(type);
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(shader));
  return shader;
}

function link(gl) {
  const program = gl.createProgram();
  gl.attachShader(program, compile(gl, gl.VERTEX_SHADER, VERTEX));
  gl.attachShader(program, compile(gl, gl.FRAGMENT_SHADER, FRAGMENT));
  gl.linkProgram(program);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(program));
  gl.useProgram(program);
  return program;
}

function grid(x0, y0, x1, y1, columns, rows) {
  const points = [];
  for (let row = 0; row < rows; row++) {
    for (let column = 0; column < columns; column++) {
      const left = x0 + (x1 - x0) * column / columns, right = x0 + (x1 - x0) * (column + 1) / columns;
      const top = y0 + (y1 - y0) * row / rows, bottom = y0 + (y1 - y0) * (row + 1) / rows;
      points.push(left, top, right, top, left, bottom, right, top, right, bottom, left, bottom);
    }
  }
  return points;
}

function upload(gl, [part, x0, y0, x1, y1]) {
  const [columns, rows] = part === BODY ? BODY_GRID : [1, 1];
  const points = grid(x0, y0, x1, y1, columns, rows);
  const buffer = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(points), gl.STATIC_DRAW);
  return { part, buffer, count: points.length / 2 };
}

function texture(gl, image) {
  gl.bindTexture(gl.TEXTURE_2D, gl.createTexture());
  gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, true);
  for (const [name, value] of [["TEXTURE_WRAP_S", "CLAMP_TO_EDGE"], ["TEXTURE_WRAP_T", "CLAMP_TO_EDGE"], ["TEXTURE_MIN_FILTER", "LINEAR"], ["TEXTURE_MAG_FILTER", "LINEAR"]]) {
    gl.texParameteri(gl.TEXTURE_2D, gl[name], gl[value]);
  }
  gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, image);
}

function createScene(canvas, image) {
  const gl = canvas.getContext("webgl", { alpha: true, premultipliedAlpha: true, antialias: true, powerPreference: "low-power" });
  if (!gl) return null;
  const program = link(gl);
  const at = name => gl.getUniformLocation(program, name);
  const uniforms = { time: at("uTime"), part: at("uPart") };
  const position = gl.getAttribLocation(program, "aPos");
  gl.enableVertexAttribArray(position);
  const meshes = PARTS.map(part => upload(gl, part));
  texture(gl, image);
  gl.enable(gl.BLEND);
  gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
  gl.clearColor(0, 0, 0, 0);
  return {
    gl,
    draw(seconds) {
      gl.viewport(0, 0, canvas.width, canvas.height);
      gl.clear(gl.COLOR_BUFFER_BIT);
      gl.uniform1f(uniforms.time, seconds % LOOP_SECONDS);
      for (const mesh of meshes) {
        gl.uniform1i(uniforms.part, mesh.part);
        gl.bindBuffer(gl.ARRAY_BUFFER, mesh.buffer);
        gl.vertexAttribPointer(position, 2, gl.FLOAT, false, 0, 0);
        gl.drawArrays(gl.TRIANGLES, 0, mesh.count);
      }
    },
    release() { gl.getExtension("WEBGL_lose_context")?.loseContext(); },
  };
}

function fit(canvas) {
  const ratio = Math.min(devicePixelRatio || 1, MAX_PIXEL_RATIO);
  const width = Math.round(canvas.clientWidth * ratio), height = Math.round(canvas.clientHeight * ratio);
  if (canvas.width === width && canvas.height === height) return;
  canvas.width = width;
  canvas.height = height;
}

function motionAllowed() {
  return !reduce.matches && !document.documentElement.classList.contains("vis");
}

function animate(root, canvas, image) {
  let scene;
  try { scene = createScene(canvas, image); } catch { scene = null; }
  if (!scene) return;
  let seconds = 0, last = 0, frame = 0, visible = true;
  const sizer = new ResizeObserver(() => fit(canvas));
  const watcher = new IntersectionObserver(([entry]) => { visible = entry.isIntersecting; update(); });
  function tick(now) {
    if (!root.isConnected) { stop(); return; }
    if (last) seconds += (now - last) / 1000;
    last = now;
    scene.draw(seconds);
    frame = requestAnimationFrame(tick);
  }
  function update() {
    if (!root.isConnected) { stop(); return; }
    const moving = visible && motionAllowed() && !document.hidden;
    root.classList.toggle("is-live", motionAllowed());
    root.classList.toggle("is-moving", moving);
    cancelAnimationFrame(frame);
    last = 0;
    if (moving) frame = requestAnimationFrame(tick);
  }
  function stop() {
    running.delete(root);
    cancelAnimationFrame(frame);
    sizer.disconnect();
    watcher.disconnect();
    reduce.removeEventListener("change", update);
    document.removeEventListener("visibilitychange", update);
    document.removeEventListener("club-vision-change", update);
    scene.release();
  }
  canvas.addEventListener("webglcontextlost", () => { root.classList.remove("is-live", "is-moving"); stop(); }, { once: true });
  running.set(root, stop);
  fit(canvas);
  sizer.observe(canvas);
  watcher.observe(root);
  reduce.addEventListener("change", update);
  document.addEventListener("visibilitychange", update);
  document.addEventListener("club-vision-change", update);
  scene.draw(seconds);
  update();
}

function whenLoaded(image, start) {
  if (image.complete && image.naturalWidth) { start(); return; }
  image.addEventListener("load", start, { once: true });
}

function releaseDetached() {
  for (const [root, stop] of running) if (!root.isConnected) stop();
}

export function prepareThemis() {
  releaseDetached();
  for (const root of document.querySelectorAll(".lux-themis:not([data-themis-ready])")) {
    root.dataset.themisReady = "true";
    const canvas = root.querySelector(".lux-themis__scene");
    const image = root.querySelector(".lux-themis__poster");
    if (canvas && image && "IntersectionObserver" in window && "ResizeObserver" in window) {
      whenLoaded(image, () => animate(root, canvas, image));
    }
  }
}
