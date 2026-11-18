"""The built-in profiles. Signal names match `recaply.signals` plugins; a
weight for a signal that isn't available for a video (no chat replay, say)
is simply dropped and the rest are re-normalised."""

from recaply.profiles.base import Profile, register
from recaply.profiles.valorant.vocab import VOCAB as VALORANT_VOCAB

HYPE = ("no way", "oh my god", "what", "let's go", "lets go", "insane", "clip that", "clip it",
        "crazy", "holy", "bro", "dude", "yes", "no no no", "wait", "how", "unbelievable")

TALK = register(Profile(
    key="talk", label="Podcast / interview / talk", kind="talk",
    brief=("A podcast, interview, webinar or talk. Great clips have a strong hook in the first "
           "seconds, a complete thought (story, hot take, surprising fact, advice, a funny exchange "
           "or a quotable line) and make sense without the rest of the video."),
    weights={"transcript": 0.7, "audio_energy": 0.15, "chat_rate": 0.15},
    keywords=("the truth is", "nobody", "secret", "mistake", "never", "always", "story", "crazy",
              "honestly", "the problem", "here's the thing", "biggest", "worst", "best", "why"),
    clip_min=20, clip_max=75, clip_target=45, window_pre=45, window_post=45,
    transcribe_all=True, reframe="face"))

GAMING = register(Profile(
    key="gaming", label="Gaming (any game)", kind="gaming",
    brief=("A gaming stream or video. Great clips are big plays, funny fails, rage or hype "
           "reactions, clutch moments and surprising events, ideally with the streamer reacting. "
           "The payoff must be inside the clip."),
    weights={"chat_rate": 0.3, "audio_energy": 0.3, "transcript": 0.25, "motion": 0.15},
    keywords=HYPE, clip_min=15, clip_max=60, clip_target=30, window_pre=35, window_post=20,
    reframe="split"))

FPS = register(Profile(
    key="fps", label="Shooter (CS2, Apex, Fortnite, CoD...)", kind="gaming",
    brief=("A first-person/third-person shooter. Great clips are multi-kills, clutches, insane "
           "flicks, close calls and big reactions. Start just before the action begins."),
    weights={"chat_rate": 0.3, "audio_energy": 0.35, "transcript": 0.2, "motion": 0.15},
    keywords=HYPE + ("ace", "clutch", "one tap", "headshot", "1v", "four k", "triple", "quad"),
    clip_min=12, clip_max=45, clip_target=25, window_pre=30, window_post=15, reframe="split"))

OPEN_WORLD = register(Profile(
    key="open_world", label="Story / open world (GTA, RDR, RP...)", kind="gaming",
    brief=("An open-world or story game stream (GTA, roleplay, RDR...). Great clips are funny "
           "interactions, chaos, chases, unexpected events and story beats with a clear setup "
           "and payoff. Dialogue matters as much as action."),
    weights={"transcript": 0.35, "chat_rate": 0.3, "audio_energy": 0.25, "motion": 0.1},
    keywords=HYPE + ("cops", "chase", "heist", "bank", "run", "crash"),
    vocab="GTA, Los Santos, Vice City, Leonida, Lucia, Jason, roleplay, NoPixel",
    clip_min=20, clip_max=75, clip_target=40, window_pre=45, window_post=25, reframe="split"))

IRL = register(Profile(
    key="irl", label="IRL / variety / just chatting", kind="irl",
    brief=("An IRL, variety or just-chatting stream. Great clips are funny or awkward moments, "
           "surprising encounters, strong reactions and good stories."),
    weights={"chat_rate": 0.35, "audio_energy": 0.3, "transcript": 0.35},
    keywords=HYPE, clip_min=15, clip_max=60, clip_target=35, window_pre=40, window_post=25,
    reframe="face"))

VALORANT = register(Profile(
    key="valorant", label="Valorant (streams and POV)", kind="gaming",
    brief=("A Valorant stream or POV video. Great clips are aces, 4Ks, clutches (1vX), "
           "outplays and big reactions; start at the beginning of the fight."),
    weights={"chat_rate": 0.3, "audio_energy": 0.35, "transcript": 0.2, "motion": 0.15},
    keywords=HYPE + ("ace", "clutch", "1v", "four k", "spike", "defuse", "op", "operator"),
    vocab=VALORANT_VOCAB, clip_min=12, clip_max=50, clip_target=28, window_pre=30, window_post=15,
    reframe="split", detectors=("valorant_killfeed",)))

VALORANT_VCT = register(Profile(
    key="valorant_vct", label="Valorant VCT broadcast (rounds)", kind="gaming",
    brief="A VCT broadcast; whole rounds ranked by kills, clutches and crowd noise.",
    weights={"valorant_rounds": 0.8, "audio_energy": 0.2},
    vocab=VALORANT_VOCAB, clip_min=30, clip_max=180, clip_target=120,
    reframe="center", detectors=("valorant_overlay", "valorant_killfeed"),
    segmenter="valorant_rounds", hidden=True))
