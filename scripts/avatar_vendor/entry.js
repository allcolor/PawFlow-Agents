import { TalkingHead } from '../TalkingHead/modules/talkinghead.mjs';
import { HeadAudio } from '../HeadAudio/dist/headaudio.min.mjs';
import { MotionEngine } from '../MotionEngine/src/MotionEngine.js';
import motions from '../MotionEngine/src/motions_th.json';

globalThis.PawflowAvatarVendor = Object.freeze({
  TalkingHead,
  HeadAudio,
  MotionEngine,
  motions,
  versions: Object.freeze({
    talkingHead: 'v1.7.0@67a210b91486a42e58d38fd5682fbfc6754f67bd',
    headAudio: '0.1.0@d3af5f9ff86ab6b2b1913d411a4e1922ec101953',
    motionEngine: '0.3.0@bd780a19e10d1cc5736a77946b04e08d658d5bf8',
    three: '0.180.0',
  }),
});
