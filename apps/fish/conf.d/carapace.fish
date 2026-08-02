# carapace: universal completion engine. Generates completions for ~700
# CLIs from a unified spec, filling in gaps where tools don't ship native
# fish completions. No-op when carapace is missing.
#
# carapace's init runs `complete -e` on every command it claims (~16k names)
# before registering its own, so a thinner carapace spec silently replaces a
# richer fish completion. CARAPACE_EXCLUDES is what keeps fish's own flags for
# those commands, and it must be set before the init is sourced, because the
# erasure happens there. The list is measured; see apps/fish/carapace-excludes.txt.
#
# Set NO_CARAPACE to skip carapace entirely. bin/gen-carapace-excludes.fish uses
# it as the control side of its measurement, so the comparison differs in
# carapace alone rather than in the whole config.
if command -q carapace; and not set -q NO_CARAPACE
    set -gx CARAPACE_BRIDGES 'inshellisense,fish,zsh,bash'

    # An inherited CARAPACE_EXCLUDES wins, so the generator can ask for the
    # unmodified carapace baseline by exporting it empty.
    set -l excludes_file (path dirname (path resolve (status filename)))/../carapace-excludes.txt
    if test -f $excludes_file; and not set -q CARAPACE_EXCLUDES
        set -l excluded (string replace -r '#.*' '' <$excludes_file | string trim | string match -rv '^$')
        test (count $excluded) -gt 0; and set -gx CARAPACE_EXCLUDES (string join , $excluded)
    end

    carapace _carapace fish | source
end
