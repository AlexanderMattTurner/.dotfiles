#!/usr/bin/env fish
#
# Regenerate apps/fish/carapace-excludes.txt.
#
# carapace erases fish's own completion for every command it claims, then
# registers its own spec. That is a large win for most commands and a loss for
# a minority, where fish already knew more flags than carapace's spec carries.
# This script counts the flag candidates both ways for every command on PATH
# and records each command where carapace offers strictly fewer.
#
# The comparison shells out twice over the same config and the same PATH, with
# NO_CARAPACE toggling the one variable under test. Do not reach for
# --no-config as the control: it leaves $fish_complete_path empty, so the run
# sees only fish's embedded completions and misses every completion that lives
# in the user's completions dir or in the generated man-page cache.
#
# Takes a few minutes. Run it after a carapace upgrade or a large tool install.

set -l repo (path dirname (path dirname (path resolve (status filename))))
set -l out $repo/apps/fish/carapace-excludes.txt

if not command -q carapace
    echo "carapace is not installed; nothing to measure." >&2
    exit 1
end

set -l probe (mktemp -t carapace-probe.XXXXXX.fish)
# Every executable on PATH, with the number of candidates it offers after a
# bare "-". Deduplicated by name, first hit on PATH wins, as fish resolves it.
echo '
set -l seen
for d in $PATH
    test -d $d; or continue
    for f in $d/*
        test -x $f; and not test -d $f; or continue
        set -l b (path basename $f)
        contains -- $b $seen; and continue
        set -a seen $b
        echo $b (complete -C "$b -" | count)
    end
end' >$probe

set -l with (mktemp -t carapace-with.XXXXXX)
set -l without (mktemp -t carapace-without.XXXXXX)

# The measured run must not read the list it is about to rewrite, or a command
# already excluded looks like a command carapace never claimed.
#
# LC_ALL=C throughout: `join` drops rows without warning when its collation
# disagrees with the one `sort` used, which would silently shorten the list.
echo "Measuring with carapace..." >&2
env -u NO_CARAPACE CARAPACE_EXCLUDES= fish $probe 2>/dev/null | env LC_ALL=C sort >$with
echo "Measuring without carapace..." >&2
env NO_CARAPACE=1 fish $probe 2>/dev/null | env LC_ALL=C sort >$without

set -l losers (env LC_ALL=C join $with $without | awk '$2+0 < $3+0 {print $1}' | env LC_ALL=C sort)

begin
    echo '# Commands that carapace must not handle.'
    echo '#'
    echo "# carapace erases fish's own completion for every command it claims, then"
    echo '# substitutes its own spec. For most commands that is a large win. For the'
    echo '# commands below the spec is thinner than what fish already knew, so the flags'
    echo '# disappear from the Tab menu.'
    echo '#'
    echo '# Regenerate with bin/gen-carapace-excludes.fish, which measures both sides.'
    echo "# Format: one command per line. '#' starts a comment. Blank lines are ignored."
    echo ''
    printf '%s\n' $losers
end >$out

command rm -f $probe $with $without
echo "Wrote "(count $losers)" excluded commands to $out" >&2
