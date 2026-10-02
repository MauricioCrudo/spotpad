from edl import parse_session_text, clip_at_selection
S = """SESSION NAME:\tR1_Foley
SAMPLE RATE:\t48000.000000

T R A C K  L I S T I N G
TRACK NAME:\tHANDS SPOT
COMMENTS:\t
USER DELAY:\t0 Samples
STATE: \t
PLUG-INS: \t
CHANNEL \tEVENT   \tCLIP NAME                     \tSTART TIME    \tEND TIME      \tDURATION      \tSTATE
1       \t1       \tHands clap                    \t48000         \t96000         \t48000         \tUnmuted
1       \t2       \tHands surface wood            \t120000        \t200000        \t80000         \tUnmuted


TRACK NAME:\tHANDS 1
COMMENTS:\t
USER DELAY:\t0 Samples
STATE: \t
PLUG-INS: \t
CHANNEL \tEVENT   \tCLIP NAME                     \tSTART TIME    \tEND TIME      \tDURATION      \tSTATE
"""
t = parse_session_text(S)
assert list(t) == ["HANDS SPOT", "HANDS 1"], t
assert t["HANDS 1"] == []
ev = t["HANDS SPOT"]
assert clip_at_selection(ev, 120000, 200000).clip == "Hands surface wood"
assert clip_at_selection(ev, 50000, 50000).clip == "Hands clap"      # cursor
assert clip_at_selection(ev, 90000, 130000).clip == "Hands surface wood"  # más overlap
assert clip_at_selection(ev, 0, 10) is None
print("edl OK")
