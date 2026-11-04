# {{PROJECT_NAME}}

Three.js + Vite starter — render loop, perspective camera, OrbitControls,
spinning cube, ground plane.

## Setup

Needs Node.js 20.19+ or 22.12+ (Vite 8's minimum).

```
npm install
```

## Run

```
npm run dev      # dev server on http://localhost:5173
npm run build    # production bundle into dist/
npm run preview  # preview the built bundle
```

ALPACKA's globe button on the project card opens the dev URL once the
server is up.

## Tour

- `index.html` — single mount point + global styles
- `src/main.js` — renderer, scene, camera, controls, lights, render loop

## Customizing

- Drop in a GLTF: `import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js"`
- Add post-processing: `import { EffectComposer } from "three/addons/postprocessing/EffectComposer.js"`
- For TypeScript: `npm install -D typescript @types/three`, rename `.js` → `.ts`,
  add a minimal `tsconfig.json`, point `index.html` at `/src/main.ts`
- For React Three Fiber: switch to the `vite-react` template and add
  `npm install three @react-three/fiber @react-three/drei`
