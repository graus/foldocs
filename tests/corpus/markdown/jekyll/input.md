---
layout: post
title:  "Welcome to Jekyll!"
date:   2026-10-01 10:00:00 +0200
categories: jekyll update
excerpt_separator: <!--more-->
---

* TOC
{:toc}

You'll find this post in your `_posts` directory. Go ahead and edit it.

<!--more-->

{% highlight ruby %}
def print_hi(name)
  puts "Hi, #{name}"
end
print_hi('Jan')
{% endhighlight %}

{% include note.html content="A note from an include" %}

Built for {{ site.title }} in {{ page.date | date: "%Y" }}.

A paragraph with a class.
{: .lead }

Check out the [Jekyll docs][jekyll-docs] for more.

[jekyll-docs]: https://example.com/docs/home
