"""The fixed text of the access script init runs on a Proxmox VE host as root, around the calls engine/access.py renders
from its list (docs/architecture/engine.md §16.5).

The script is `/bin/sh`, run as `sh -s` with itself on standard input, over root SSH or by a person who pasted what
`aw init --print-commands` printed: so every command it starts reads `/dev/null`, or a pipe, and never the rest of the
script. It reads every answer of Proxmox's command line as JSON (`--output-format json`) through one Perl program,
JSON::PP, core Perl, which every Proxmox VE host has, Proxmox being Perl: never through grep or sed. The program
(`PERL`) answers one question a call: a role's privileges it lacks, or holds beyond its own; the pool's state by the
fleet's mark, read as fleet/mark.py reads it (white space as Python reads it, Perl's and the separators \\x1c to
\\x1f); the comment taking an unmarked pool gives it; whether an ACL entry is there; a token's privilege
separation and the mark in its comment, read as the pool's; a person's own template by its name, and the bridges its
network cards are on; a new token's secret; and a command's words, quoted as Python's shlex.quote quotes them, for the
lines the script prints.

The calls of the list (engine/access.py's `objects`) are each one of `Pool`, `Role`, `User`, `Grant` and `Token`,
defined here, run twice: first to read what is there and decide, each object's decision kept by its place in the list,
then, once nothing refused, to make what was decided, in the list's order. A refusal ends the run before anything is
made. `render` puts the rendered parts, the header (engine/access.py's, said through the printer, since it names init's
lines), the values and patterns as assignments and the two functions `sentence` and `objects`, between the fixed parts
here.

Standard library only; it imports nothing of the project's. Its text names the paths of Proxmox's command line,
which no file of the engine's names outside pve/.
"""

from __future__ import annotations

from collections.abc import Sequence

__all__ = ["PERL", "pool_read", "render"]

# The one Perl program: `perl -e "$pl" -- <question> <words...>`, the JSON on standard input where the question reads
# one. No single quote in it, since the script holds it in one: chr 39 is the quote.
PERL = r"""use strict;
use warnings;
my ($question, @words) = @ARGV;
my $q = chr 39;
my $uuid = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}";
# White space as Python reads it in a str: that of Perl, and the separators \x1c to \x1f.
my $space = qr/[\s\x1c-\x1f]/u;
my $nonspace = qr/[^\s\x1c-\x1f]/u;
sub answer {
    require JSON::PP;
    local $/;
    my $text = <STDIN>;
    return JSON::PP->new->utf8->decode(defined $text ? $text : "");
}
sub as_text { binmode STDOUT, ":encoding(UTF-8)"; }
sub held {
    my ($value, $pattern) = @_;
    return defined $value && !ref $value && $value =~ /\A(?:$pattern)\z/;
}
sub one_line {
    my ($text, $most) = @_;
    $text =~ s/[^!-~]+/ /g;
    $text =~ s/\A //;
    $text =~ s/ \z//;
    if (length($text) > $most) {
        $text = substr($text, 0, $most - 1);
        $text =~ s/ +\z//;
        $text .= "\x{2026}";
    }
    return $text;
}
sub quoted {
    my ($word) = @_;
    return $q . $q if $word eq "";
    return $word if $word =~ m{\A[A-Za-z0-9_\@%+=:,./-]+\z};
    $word =~ s/$q/$q"$q"$q/g;
    return $q . $word . $q;
}
# The state of a comment by the mark of a fleet, as the parse of fleet/mark.py reads it: unmarked, doubled, unreadable,
# of another fleet (other <its fleet>), naming a control VM (controlled <its ID>), or of this fleet (own); then a word.
sub marked {
    my ($comment, $fleet) = @_;
    my $begun = () = $comment =~ /agent-warehouse$space+(?:fleet|controller)=/gu;
    return "unmarked -" if $begun == 0;
    return "doubled -" if $begun > 1;
    return "unreadable -"
        unless $comment =~ /(?<!$nonspace)agent-warehouse fleet=($uuid) controller=(none|$uuid)(?!$nonspace)/u;
    my ($found, $controller) = ($1, $2);
    return "other $found" if $found ne $fleet;
    return "controlled $controller" if $controller ne "none";
    return "own -";
}
sub comment_of {
    my ($name) = @_;
    my $pools = answer();
    die "not a list of pools\n" unless ref $pools eq "ARRAY";
    for my $pool (@$pools) {
        next unless ref $pool eq "HASH" && defined $pool->{poolid} && !ref $pool->{poolid}
            && $pool->{poolid} eq $name;
        my $comment = $pool->{comment};
        return defined $comment && !ref $comment ? "$comment" : "";
    }
    return undef;
}
if ($question eq "quote") {
    print join(" ", map { quoted($_) } @words), "\n";
    exit 0;
}
if ($question eq "match") {
    exit(held($words[1], $words[0]) ? 0 : 1);
}
if ($question eq "names") {
    my ($key, $pattern) = @words;
    my $listed = answer();
    die "not a list\n" unless ref $listed eq "ARRAY";
    for my $entry (@$listed) {
        next unless ref $entry eq "HASH" && held($entry->{$key}, $pattern);
        print "$entry->{$key}\n";
    }
    exit 0;
}
if ($question eq "lacking" || $question eq "beyond") {
    my ($privileges, $most) = @words;
    my %wanted = map { $_ => 1 } split /,/, $privileges;
    my $held = answer();
    die "not a role\n" unless ref $held eq "HASH";
    if ($question eq "lacking") {
        print join(",", sort grep { !exists $held->{$_} } keys %wanted), "\n";
    } else {
        as_text();
        print one_line(join(", ", sort grep { !$wanted{$_} } keys %$held), $most), "\n";
    }
    exit 0;
}
if ($question eq "pool") {
    my ($name, $fleet) = @words;
    my $comment = comment_of($name);
    if (!defined $comment) { print "missing -\n"; exit 0; }
    print marked($comment, $fleet), "\n";
    exit 0;
}
if ($question eq "taken") {
    my ($name, $line, $most) = @words;
    my $comment = comment_of($name);
    die "no such pool\n" unless defined $comment;
    my $taken = ($comment eq "" || $comment =~ /$space\z/u) ? $comment . $line : "$comment $line";
    # Longer than the script writes: said by the shell, by its status alone.
    exit 3 if length($taken) > $most;
    as_text();
    print($taken);
    exit 0;
}
if ($question eq "acl") {
    my ($path, $role, $subject) = @words;
    my $entries = answer();
    die "not a list of ACL entries\n" unless ref $entries eq "ARRAY";
    for my $entry (@$entries) {
        next unless ref $entry eq "HASH";
        my %seen = map { $_ => (defined $entry->{$_} && !ref $entry->{$_} ? "$entry->{$_}" : "") }
            qw(path roleid ugid propagate);
        exit 0 if $seen{path} eq $path && $seen{roleid} eq $role && $seen{ugid} eq $subject
            && $seen{propagate} eq "1";
    }
    exit 1;
}
if ($question eq "grants") {
    my ($roles, $user, $token, $pattern, @listed) = @words;
    my %role = map { $_ => 1 } split /,/, $roles;
    my %kind = ($user => "users", $token => "tokens");
    my (%made, %seen, @left);
    while (@listed) {
        my ($path, $role, $subject) = splice @listed, 0, 3;
        $made{"$path $role $subject"} = 1;
    }
    my $entries = answer();
    die "not a list of ACL entries\n" unless ref $entries eq "ARRAY";
    for my $entry (@$entries) {
        next unless ref $entry eq "HASH";
        my ($path, $role, $subject) = map { defined $entry->{$_} && !ref $entry->{$_} ? "$entry->{$_}" : "" }
            qw(path roleid ugid);
        next unless $role{$role} && exists $kind{$subject} && held($path, $pattern);
        my $key = "$path $role $subject";
        next if $made{$key} || $seen{$key}++;
        push @left, [$path, $role, $subject];
    }
    for my $each (sort { $a->[0] cmp $b->[0] || $a->[1] cmp $b->[1] || $a->[2] cmp $b->[2] } @left) {
        print "$each->[0] $each->[1] $kind{$each->[2]} $each->[2]\n";
    }
    exit 0;
}
if ($question eq "token") {
    my ($fleet) = @words;
    my $token = answer();
    die "not a token\n" unless ref $token eq "HASH";
    my $privsep = $token->{privsep};
    my $comment = defined $token->{comment} && !ref $token->{comment} ? "$token->{comment}" : "";
    my ($state) = split / /, marked($comment, $fleet);
    print((defined $privsep && !ref $privsep && "$privsep" eq "1") ? "1" : "0", " $state\n");
    exit 0;
}
if ($question eq "template") {
    my ($name, $node) = @words;
    my $guests = answer();
    die "not a list of guests\n" unless ref $guests eq "ARRAY";
    my @named = grep {
        ref $_ eq "HASH" && ($_->{type} // "") eq "qemu" && ($_->{name} // "") eq $name
    } @$guests;
    exit 1 unless @named == 1 && $named[0]{template} && ($named[0]{node} // "") eq $node
        && held($named[0]{vmid}, "[1-9][0-9]{2,8}");
    my $pool = held($named[0]{pool}, "[A-Za-z0-9_.-]+") ? $named[0]{pool} : "";
    print "$named[0]{vmid} $pool\n";
    exit 0;
}
if ($question eq "bridges") {
    my ($pattern) = @words;
    my $config = answer();
    die "not a configuration\n" unless ref $config eq "HASH";
    my %said;
    for my $index (sort { $a <=> $b } map { /\Anet([0-9]+)\z/ ? $1 : () } keys %$config) {
        my $card = $config->{"net$index"};
        next unless defined $card && !ref $card && $card =~ /(?:\A|,)bridge=([^,]*)/;
        my $bridge = $1;
        $bridge = "!" unless held($bridge, $pattern);
        print "$bridge\n" unless $said{$bridge}++;
    }
    exit 0;
}
if ($question eq "secret") {
    my $made = answer();
    my $value = ref $made eq "HASH" ? $made->{value} : undef;
    exit 1 unless held($value, $uuid);
    print "$value\n";
    exit 0;
}
die "no such question\n";
"""

# From the values to the interpreter: the helpers, the options, the checks of each value and of the host, the reads of
# what the list is made from, and the five calls.
BODY = r"""
say() { printf '%s\n' "$*" >&2; }
die() {
  say "error: $*"
  exit 1
}
# refuse <sentence>: one of the list's own sentences, said as it is; nothing was changed. Each of these takes its
# words as one or more arguments, joined by a space.
refuse() {
  say "$*"
  exit 1
}
# answer <question> <words>: Perl's answer to one question of the JSON on stdin; plain <question> <words>: of words.
answer() { perl -e "$pl" -- "$@"; }
plain() { perl -e "$pl" -- "$@" </dev/null; }
# put <name> <value>, got <name>: a value kept under a name made of the list's own words, read back into $got.
put() { eval "$1=\$2"; }
got() { eval "got=\${$1-}"; }

mode=apply no_token=0 take=0 pool_given=0 regenerate=0
while [ $# -gt 0 ]; do
  case "$1" in
  --storage | --bridge | --user | --token | --pool | --audit-scope)
    [ $# -ge 2 ] || die "$1 takes a value"
    case "$1" in
    --storage) storage=$2 ;;
    --bridge) bridge=$2 ;;
    --user) user=$2 ;;
    --token) token=$2 ;;
    --pool) pool=$2 pool_given=1 ;;
    --audit-scope) audit_scope=$2 ;;
    esac
    shift 2
    ;;
  --take-unmarked)
    take=1
    shift
    ;;
  --no-token)
    no_token=1
    shift
    ;;
  --regenerate)
    regenerate=1
    shift
    ;;
  --dry-run)
    mode=dry-run
    shift
    ;;
  --list)
    mode=list
    shift
    ;;
  *)
    # Said back only where it is a word of letters and hyphens, as an option is.
    case "$1" in
    '' | [!-]* | -[!-]* | --*[!abcdefghijklmnopqrstuvwxyz-]*)
      die "a word that is no option was given: the options are at the top of this script"
      ;;
    esac
    die "no option $1: the options are at the top of this script"
    ;;
  esac
done
[ "$take" = 0 ] || [ "$pool_given" = 1 ] || die "--take-unmarked takes the pool --pool names: give --pool <name> too"
[ "$regenerate" = 0 ] || [ "$no_token" = 0 ] \
  || die "--regenerate makes the token's secret again, and --no-token makes no token: give one of them"

perl -MJSON::PP -e 1 </dev/null >/dev/null 2>&1 \
  || die "there is no Perl with JSON::PP here: run it on the Proxmox VE host, whose Perl has it"
# Each value goes on pveum's command line and into an ACL path, so each is held to the form Proxmox makes it by, and
# a refusal never says it back.
plain match "$re_pool" "$pool" || die "$(sentence refused-pool) (--pool)"
plain match "$re_user" "$user" && [ "${#user}" -le "$user_most" ] || die "$(sentence refused-user) (--user)"
[ "$user" != root@pam ] || die "$(sentence refused-root) (--user)"
plain match "$re_token" "$token" || die "$(sentence refused-token) (--token)"
plain match "$re_storage" "$storage" || die "$(sentence refused-storage) (--storage)"
plain match "$re_bridge" "$bridge" || die "$(sentence refused-bridge) (--bridge)"
plain match "$re_scope" "$audit_scope" || die "$(sentence refused-scope) (--audit-scope)"

uid=$(id -u </dev/null) || die "id did not say who runs this"
[ "$uid" = 0 ] || die "run it as root, on the Proxmox host"
command -v pveum >/dev/null 2>&1 && command -v pvesh >/dev/null 2>&1 \
  || die "there is no pveum here: run it on the Proxmox VE host"
version=$(pveversion </dev/null) || die "pveversion did not say which Proxmox VE this is"
major=${version#pve-manager/}
major=${major%%.*}
case "$major" in '' | *[!0123456789]*) die "pveversion did not say which Proxmox VE this is" ;; esac
[ "$major" -ge 9 ] \
  || die "this is Proxmox VE $major, and the engine needs Proxmox VE 9 or later: upgrade the host first"

json=$(pvesh get "/nodes/$node/storage" --content images --enabled 1 --output-format json </dev/null) \
  || die "Proxmox would not list the storages on node $node"
storages=$(printf '%s' "$json" | answer names storage "$re_storage") || die "Proxmox's storages could not be read"
json=$(pvesh get "/nodes/$node/network" --type any_local_bridge --output-format json </dev/null) \
  || die "Proxmox would not list the bridges on node $node"
bridges_here=$(printf '%s' "$json" | answer names iface "$re_bridge") || die "Proxmox's bridges could not be read"
if [ "$mode" = list ]; then
  for name in $storages; do printf 'storage %s\n' "$name"; done
  for name in $bridges_here; do printf 'bridge %s\n' "$name"; done
  exit 0
fi

listed() { # listed <name> <names>
  for name in $2; do
    [ "$name" != "$1" ] || return 0
  done
  return 1
}
listing() { printf '%s' "$*" | sed 's/ /, /g'; }
if ! listed "$storage" "$storages"; then
  [ -n "$storages" ] || die "node $node has no storage for VM disks enabled"
  die "storage $storage is not on node $node for VM disks: give one of $(listing $storages) with --storage"
fi
if ! listed "$bridge" "$bridges_here"; then
  [ -n "$bridges_here" ] || die "node $node has no bridge"
  die "bridge $bridge is not on node $node: give one of $(listing $bridges_here) with --bridge"
fi

# A person's own template: the one guest of its name, a template on this node. Each bridge its network cards are on
# is granted as the base template's is; outside the pool, it is cloned through a role of its own on it alone.
bridges=$bridge custom_id=''
if [ -n "$custom" ]; then
  json=$(pvesh get /cluster/resources --type vm --output-format json </dev/null) \
    || die "Proxmox would not list its guests"
  rc=0
  found=$(printf '%s' "$json" | answer template "$custom" "$node") || rc=$?
  [ "$rc" != 1 ] || die "no template named $custom is on node $node: proxmox_template_custom names the one guest" \
    "of that name, a template on this node"
  [ "$rc" = 0 ] || die "Proxmox's guests could not be read"
  json=$(pvesh get "/nodes/$node/qemu/${found%% *}/config" --output-format json </dev/null) \
    || die "Proxmox would not read the configuration of template $custom"
  cards=$(printf '%s' "$json" | answer bridges "$re_bridge") \
    || die "the configuration of template $custom could not be read"
  for each in $cards; do
    [ "$each" != '!' ] \
      || die "template $custom has a network card on a bridge whose name is not one Proxmox gives a bridge"
    listed "$each" "$bridges_here" \
      || die "template $custom has a network card on bridge $each, which is not on node $node"
    listed "$each" "$bridges" || bridges="$bridges $each"
  done
  [ "${found#* }" = "$pool" ] || custom_id=${found%% *}
fi

# ------------------------------------------------------------------ the calls ---
# Each is run twice: phase=check reads what is there and keeps a decision under the call's place in the list (d_<n>),
# refusing what is not this fleet's; phase=make makes what was decided. A role that holds more than its own is said
# with the paths of its grants (paths_<role>) and those granted already (granted_<role>); one held exactly that holds
# more is withheld, with its grants.
n=0 planned=0 steps=0 pools_read=0 acl_read=0 said_roles='' withheld='' token_there=0 token_to_make=0
made_user='' made_name='' secret='' listed_grants=''
decide() {
  put "d_$n" "$1"
  [ "$1" = none ] || planned=$((planned + 1))
}
# run <command...>: printed with --dry-run; otherwise said, and run.
run() {
  steps=$((steps + 1))
  line=$(plain quote "$@") || die "Perl could not quote a command"
  if [ "$mode" = dry-run ]; then
    printf '%s\n' "$line"
    return 0
  fi
  say "+ $line"
  "$@" </dev/null >&2 || fail "$line"
}
fail() { # fail <the line that failed>
  if [ -n "$made_name" ]; then
    die "$1 failed, after the token was made: it is there with its secret unsaid, which Proxmox showed only" \
      "once. As root here, $(plain quote pveum user token delete "$made_user" "$made_name") deletes it, and this" \
      "run again then makes it anew."
  fi
  die "$1 failed, and nothing after it was run: what it said is above"
}

Pool() { # Pool <name> <comment>
  n=$((n + 1))
  if [ "$phase" = make ]; then
    got "d_$n"
    case "$got" in
    add) run pveum pool add "$1" --comment "$2" ;;
    take) run pveum pool modify "$1" --comment "$taken" ;;
    esac
    return 0
  fi
  if [ "$pools_read" = 0 ]; then
    pools=$(pvesh get /pools --output-format json </dev/null) || die "Proxmox would not list its pools"
    pools_read=1
  fi
  state=$(printf '%s' "$pools" | answer pool "$1" "$fleet") || die "Proxmox's pools could not be read"
  found=${state#* } state=${state%% *}
  case "$state" in
  missing) decide add ;;
  unmarked)
    if [ "$take" = 0 ]; then
      say "$(sentence pool-unmarked)"
      refuse "To make pool $1 this fleet's, its comment kept and the fleet's mark added at its end, run this" \
        "again with --pool $1 --take-unmarked."
    fi
    taken=$(printf '%s' "$pools" | answer taken "$1" "$mark_line" "$comment_most") || {
      [ "$?" = 3 ] || die "Proxmox's pools could not be read"
      die "pool $1's comment, the fleet's mark added at its end, would be longer than $comment_most characters, the" \
        "most this script writes in a pool's comment, since Proxmox declares no length for one: nothing was changed;" \
        "shorten the comment and run this again"
    }
    decide take
    ;;
  *)
    [ "$take" = 0 ] \
      || die "--take-unmarked takes only a pool whose comment carries no mark, and pool $1's carries one: nothing" \
        "was changed; run this again without it"
    case "$state" in
    own) decide none ;;
    other) refuse "$(sentence pool-other)" ;;
    controlled) refuse "$(sentence pool-controlled)" ;;
    doubled) refuse "$(sentence pool-doubled)" ;;
    *) refuse "$(sentence pool-unreadable)" ;;
    esac
    ;;
  esac
}

Role() { # Role <name> <privileges, joined by commas> <exact: 1, or 0>
  n=$((n + 1))
  if [ "$phase" = make ]; then
    got "d_$n"
    case "$got" in
    add) run pveum role add "$1" --privs "$2" ;;
    append)
      got "lacking_$1"
      run pveum role modify "$1" --append 1 --privs "$got"
      ;;
    esac
    return 0
  fi
  if ! role=$(pvesh get "/access/roles/$1" --output-format json </dev/null 2>/dev/null); then
    decide add
    return 0
  fi
  lacking=$(printf '%s' "$role" | answer lacking "$2") || die "Proxmox's role $1 could not be read"
  more=$(printf '%s' "$role" | answer beyond "$2" "$said_most") || die "Proxmox's role $1 could not be read"
  if [ -n "$more" ]; then
    put "more_$1" "$more"
    said_roles="$said_roles $1"
    if [ "$3" = 1 ]; then
      withheld="$withheld $1"
      decide none
      return 0
    fi
  fi
  if [ -n "$lacking" ]; then
    put "lacking_$1" "$lacking"
    decide append
  else
    decide none
  fi
}

User() { # User <name> <comment>
  n=$((n + 1))
  if [ "$phase" = make ]; then
    got "d_$n"
    [ "$got" != add ] || run pveum user add "$1" --comment "$2"
    return 0
  fi
  if pvesh get "/access/users/$1" --output-format json </dev/null >/dev/null 2>&1; then decide none; else decide add; fi
}

read_acl() {
  if [ "$acl_read" = 0 ]; then
    acl=$(pvesh get /access/acl --output-format json </dev/null) || die "Proxmox would not list its ACL"
    acl_read=1
  fi
}
Grant() { # Grant <path> <role> <users, or tokens> <the user's ID, or the token's full ID>
  n=$((n + 1))
  if [ "$phase" = make ]; then
    got "d_$n"
    [ "$got" != add ] || run pveum acl modify "$1" "--$3" "$4" --roles "$2"
    return 0
  fi
  read_acl
  listed_grants="$listed_grants $1 $2 $4"
  # there: 0 where the entry is there, propagating; 1 where it is not.
  there=0
  printf '%s' "$acl" | answer acl "$1" "$2" "$4" || there=$?
  [ "$there" -le 1 ] || die "Proxmox's ACL could not be read"
  case " $said_roles " in
  *" $2 "*)
    got "paths_$2"
    case ", $got, " in *", $1, "*) ;; *) put "paths_$2" "${got:+$got, }$1" ;; esac
    if [ "$there" = 0 ]; then
      got "granted_$2"
      case ", $got, " in *", $1, "*) ;; *) put "granted_$2" "${got:+$got, }$1" ;; esac
    fi
    ;;
  esac
  # A role withheld is granted nowhere; any other grant is made where its entry is not there as the list makes it.
  case " $withheld " in *" $2 "*) there=0 ;; esac
  if [ "$there" = 0 ]; then decide none; else decide add; fi
}

Token() { # Token <user> <ID> <comment> <privilege separation: 1, or 0>
  n=$((n + 1))
  if [ "$phase" = make ]; then
    got "d_$n"
    case "$got" in
    add) make_token "$@" ;;
    regenerate) make_token "$@" again ;;
    esac
    return 0
  fi
  if record=$(pvesh get "/access/users/$1/token/$2" --output-format json </dev/null 2>/dev/null); then
    state=$(printf '%s' "$record" | answer token "$fleet") || die "Proxmox's token could not be read"
    [ "${state%% *}" = "$4" ] || refuse "$(sentence token-not-own)"
    # Its comment: this fleet's mark, or none (a token of the person's own, made by hand); any other is another's.
    case "${state#* }" in own | unmarked) ;; *) refuse "$(sentence token-other)" ;; esac
    if [ "$regenerate" = 1 ]; then
      [ "${state#* }" = own ] \
        || die "--regenerate makes again only a token whose comment carries this fleet's mark, and the token" \
          "$(plain quote "$1!$2")'s carries none: nothing was changed"
      token_to_make=1
      decide regenerate
      return 0
    fi
    [ "$no_token" = 1 ] || token_there=1
    decide none
  elif [ "$no_token" = 1 ]; then
    die "--no-token, and the token $(plain quote "$1!$2") is not on this host: the secret init holds is no" \
      "token's here, and nothing was changed. Run this again without --no-token to make it, and give init the" \
      "secret it prints."
  else
    token_to_make=1
    decide add
  fi
}
# make_token <user> <ID> <comment> <privilege separation> [again]: the token made, or with again, its secret made again
# (--regenerate), which every other copy of the secret then lacks. Either answers JSON whose value is the new secret.
make_token() {
  if [ "${5-}" = again ]; then
    set -- pveum user token modify "$1" "$2" --regenerate --output-format json
  else
    set -- pveum user token add "$1" "$2" --privsep "$4" --comment "$3" --output-format json
  fi
  if [ "$mode" = dry-run ]; then
    run "$@"
    return 0
  fi
  steps=$((steps + 1))
  line=$(plain quote "$@")
  say "+ $line"
  made=$("$@" </dev/null) || fail "$line"
  made_user=$5 made_name=$6
  secret=$(printf '%s' "$made" | answer secret) \
    || die "$line made the token and printed no secret: it is there with its secret unsaid. As root here," \
      "$(plain quote pveum user token delete "$5" "$6") deletes it, and this run again then makes it anew."
}
"""

# The run: every object read and decided, what is left as it is said, then each decision made, the secret last.
MAIN = r"""
phase=check
objects
for name in $said_roles; do
  got "more_$name"
  more=$got
  got "paths_$name"
  paths=$got
  got "granted_$name"
  say "$(sentence "too-much-$name" "$more" "$paths" "$got")"
done
# Each grant of the engine's roles to the user or the token that this run's list does not make: said, never removed.
read_acl
left=$(printf '%s' "$acl" | answer grants "$engine_roles" "$user" "$user!$token" "$re_path" $listed_grants) \
  || die "Proxmox's ACL could not be read"
while read -r left_path left_role left_kind left_subject; do
  [ -n "$left_path" ] || continue
  line=$(plain quote pveum acl delete "$left_path" --roles "$left_role" "--$left_kind" "$left_subject") \
    || die "Perl could not quote a command"
  say "$(sentence granted-beyond "$left_path" "$left_role" "$left_subject" "$line")"
done <<LEFT
$left
LEFT
if [ "$token_there" = 1 ]; then
  say "The token $(plain quote "$user!$token") is there: Proxmox showed its secret once, when it was made, and" \
    "never again. If it is lost, as root here $(plain quote pveum user token delete "$user" "$token") deletes it," \
    "and every machine that uses it loses access; this run again then makes it anew."
fi
if [ "$mode" = dry-run ] && [ "$planned" -gt 0 ]; then
  say "The commands this would run, one an object, in the order it runs them, as root on this host:"
  [ "$token_to_make" = 0 ] \
    || say "the token's line prints JSON whose \"value\" is the token's secret, which Proxmox shows only once."
fi
phase=make n=0
objects
[ "$steps" -gt 0 ] || say "Nothing to do: the access is set up."
if [ -n "$secret" ]; then
  say "The token's secret, which Proxmox shows only this once: init asks for it."
  printf '%s\n' "$secret"
fi
"""


def pool_read(pool: str) -> list[str]:
    """The one read init makes of the host over root SSH beside the script's own (card E8): pool's comment and members,
    as JSON, which pvesh answers as the API answers `GET /pools?poolid=` (pve/answers.py's `pool` reads either). It
    decides only which question init asks before its offer; the script decides again, and refuses on its own."""
    return ["pvesh", "get", "/pools", "--poolid", pool, "--output-format", "json"]


def render(head: str, values: Sequence[tuple[str, str]], functions: str) -> str:
    """The whole script: head, its first lines, which engine/access.py says with the host's values, each value assigned
    to its shell variable (each a shell word already quoted), the Perl program, the rendered functions `sentence` and
    `objects`, defined before the body's checks say a sentence, the fixed body, and the run."""
    if "'" in PERL:
        raise ValueError("the Perl program is held in single quotes, so it holds none")
    assigned = "".join(f"{name}={word}\n" for name, word in values)
    return f"{head}set -euf\n\n{assigned}pl='{PERL}'\n{functions}{BODY}\n{MAIN}"
