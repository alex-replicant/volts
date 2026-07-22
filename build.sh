#!/bin/sh

COMPONENTS="prepare vp report database media sipp opensips"
OPTIONAL_COMPONENTS="scripter"
REGISTRY="ihorolkhovskyi"

# Determine if we're building with podman or docker.
# Podman stores locally built images under the "localhost/" namespace and
# enforces short-name resolution, so local images must be fully qualified.
if docker --version 2>/dev/null | grep -qi "podman"; then
    IMAGE_PREFIX="localhost/"
else
    IMAGE_PREFIX=""
fi

usage() {
    echo "Usage: $0 [-c|--clean] [-r|--refresh [component,...]] [-s|--with-scripts] [-p|--push]"
    echo ""
    echo "Options:"
    echo "  -c, --clean                   Stop/remove all VOLTS containers and images"
    echo "  -r, --refresh                 Force rebuild all components (--no-cache)"
    echo "  -r, --refresh comp1[,comp2,...]  Force rebuild specific component(s) (--no-cache)"
    echo "  -s, --with-scripts            Include the optional scripter component"
    echo "  -p, --push                    Tag and push images to $REGISTRY"
    echo ""
    echo "Components: $COMPONENTS $OPTIONAL_COMPONENTS"
    exit 1
}

REBUILD_COMPONENTS=""
CLEAN=0
PUSH=0
WITH_SCRIPTS=0
REFRESH_ALL=0

is_valid_component() {
    comp="$1"
    for c in $COMPONENTS $OPTIONAL_COMPONENTS; do
        [ "$c" = "$comp" ] && return 0
    done
    return 1
}

while [ $# -gt 0 ]; do
    case "$1" in
        -c|--clean)
            CLEAN=1
            shift
            ;;
        -s|--with-scripts)
            WITH_SCRIPTS=1
            shift
            ;;
        -r|--refresh)
            shift
            if [ -n "$1" ] && [ "${1#-}" = "$1" ]; then
                REBUILD_COMPONENTS=$(echo "$1" | tr ',' ' ')
                shift
                for comp in $REBUILD_COMPONENTS; do
                    if ! is_valid_component "$comp"; then
                        echo "Unknown component: $comp"
                        echo "Valid components are: $COMPONENTS $OPTIONAL_COMPONENTS"
                        exit 1
                    fi
                done
            else
                REFRESH_ALL=1
            fi
            ;;
        -p|--push)
            PUSH=1
            shift
            ;;
        -h|--help)
            usage
            ;;
        *)
            echo "Unknown option: $1"
            usage
            ;;
    esac
done

# Order-independent: finalize COMPONENTS after parsing so -s / -r work in any order
if [ "$WITH_SCRIPTS" = "1" ]; then
    COMPONENTS="$COMPONENTS scripter"
elif echo " $REBUILD_COMPONENTS " | grep -q " scripter "; then
    COMPONENTS="$COMPONENTS scripter"
fi
if [ "$REFRESH_ALL" = "1" ]; then
    REBUILD_COMPONENTS="$COMPONENTS"
fi

if [ "$CLEAN" = "1" ]; then
    echo "Cleaning VOLTS containers and images..."
    # Always include optional images (e.g. scripter) even when -s was not passed
    for comp in $COMPONENTS $OPTIONAL_COMPONENTS; do
        docker ps -q --filter "ancestor=${IMAGE_PREFIX}volts_$comp" | xargs -r docker stop
        docker ps -aq --filter "ancestor=${IMAGE_PREFIX}volts_$comp" | xargs -r docker rm
        docker image rm "${IMAGE_PREFIX}volts_$comp:latest" >> /dev/null 2>&1
    done
    exit 0
fi

should_rebuild() {
    comp="$1"
    [ -z "$REBUILD_COMPONENTS" ] && return 1
    for c in $REBUILD_COMPONENTS; do
        [ "$c" = "$comp" ] && return 0
    done
    return 1
}

for comp in $COMPONENTS; do
    tag="${IMAGE_PREFIX}volts_$comp"
    cache_opt=""

    if should_rebuild "$comp"; then
        docker image rm "$tag:latest" >> /dev/null 2>&1
        cache_opt="--no-cache"
    fi

    docker build $cache_opt --file "build/Dockerfile.$comp" --platform linux/amd64 --tag "$tag" build/
done

if [ "$PUSH" = "1" ]; then
    for comp in $COMPONENTS; do
        remote_tag="$REGISTRY/$comp"
        docker tag "${IMAGE_PREFIX}volts_$comp" "$remote_tag"
        docker push "$remote_tag"
    done
fi
