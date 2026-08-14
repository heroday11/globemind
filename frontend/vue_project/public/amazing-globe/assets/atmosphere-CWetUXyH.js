const o=`#define GLSLIFY 1
uniform vec3 glowColor;varying vec3 vNormal;varying vec3 vPosition;void main(){vec3 viewDirection=normalize(-vPosition);float intensity=pow(0.68-dot(vNormal,viewDirection),8.0);gl_FragColor=vec4(glowColor*intensity,intensity);}`,i=`#define GLSLIFY 1
varying vec3 vNormal;varying vec3 vPosition;void main(){vNormal=normalize(normalMatrix*normal);vec4 mvPosition=modelViewMatrix*vec4(position,1.0);vPosition=mvPosition.xyz;gl_Position=projectionMatrix*mvPosition;}`;export{o as a,i as b};
