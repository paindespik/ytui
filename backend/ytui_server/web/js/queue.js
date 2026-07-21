// Client-side play queue — exact port of mobile/lib/state/queue.dart.
// Emits a "change" event on every mutation.

class PlayQueue extends EventTarget {
  items = [];
  index = 0;

  get current() {
    return this.index >= 0 && this.index < this.items.length ? this.items[this.index] : null;
  }
  get hasNext() { return this.index + 1 < this.items.length; }
  get hasPrevious() { return this.index > 0; }
  get upcoming() { return this.items.slice(this.index + 1); }

  _emit() { this.dispatchEvent(new Event("change")); }

  play(videos, startIndex = 0) {
    this.items = [...videos];
    this.index = startIndex;
    this._emit();
  }

  enqueue(video) {
    this.items = [...this.items, video];
    this._emit();
  }

  // Jump to the item at targetIndex (an index into the FULL items list) and
  // play it now. Filtering is POSITIONAL so duplicate videos survive and an
  // already-played item can be jumped to without duplication (mirrors the
  // mobile QueueNotifier.jumpTo semantics — never dedup by value).
  jumpTo(targetIndex) {
    if (targetIndex < 0 || targetIndex >= this.items.length) return;
    if (targetIndex === this.index) return;
    const target = this.items[targetIndex];
    const history = [];
    for (let i = 0; i <= this.index; i++) {
      if (i !== targetIndex) history.push(this.items[i]);
    }
    const upcoming = [];
    for (let i = this.index + 1; i < this.items.length; i++) {
      if (i !== targetIndex) upcoming.push(this.items[i]);
    }
    this.items = [...history, target, ...upcoming];
    this.index = history.length;
    this._emit();
  }

  // Remove the upcoming item at `offset` (0 = the next one). Positional.
  removeUpcoming(offset) {
    const i = this.index + 1 + offset;
    if (i <= this.index || i >= this.items.length) return;
    this.items = this.items.filter((_, j) => j !== i);
    this._emit();
  }

  next() {
    if (this.hasNext) {
      this.index += 1;
      this._emit();
    }
  }

  previous() {
    if (this.hasPrevious) {
      this.index -= 1;
      this._emit();
    }
  }

  clear() {
    this.items = [];
    this.index = 0;
    this._emit();
  }
}

export const queue = new PlayQueue();
