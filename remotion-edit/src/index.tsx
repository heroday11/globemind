import {Composition, registerRoot} from 'remotion';
import {GlobemindDemo} from './GlobemindDemo';

export const RemotionRoot = () => {
  return (
    <Composition
      id="GlobemindDemo"
      component={GlobemindDemo}
      durationInFrames={4110}
      fps={30}
      width={1920}
      height={1080}
    />
  );
};

registerRoot(RemotionRoot);
