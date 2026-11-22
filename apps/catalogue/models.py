from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models


class Category(models.Model):
    """Product category. New categories need only a new row, no code change."""

    slug = models.SlugField(max_length=60, unique=True)
    name = models.CharField(max_length=80)
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name_plural = "categories"

    def __str__(self):
        return self.name


class Presentation(models.Model):
    """How a product is presented, e.g. Bouquet, Box, Vase."""

    slug = models.SlugField(max_length=60, unique=True)
    name = models.CharField(max_length=80)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Product(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PUBLISHED = "published", "Published"
        RETIRED = "retired", "Retired"

    class StockPolicy(models.TextChoices):
        MADE_TO_ORDER = "made_to_order", "Made to order (no stock count)"
        TRACKED = "tracked", "Tracked stock (per variant)"

    slug = models.SlugField(max_length=80, unique=True, help_text="Stable public ID")
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="products")
    name = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    ingredients = models.JSONField(default=list, blank=True)
    palette = models.JSONField(default=list, blank=True)
    occasions = models.JSONField(default=list, blank=True)

    is_seasonal = models.BooleanField(default=False)
    season_note = models.CharField(max_length=160, blank=True)

    image_url = models.URLField(max_length=500, blank=True)
    image_alt = models.CharField(max_length=200, blank=True)

    stock_policy = models.CharField(max_length=20, choices=StockPolicy.choices, default=StockPolicy.MADE_TO_ORDER)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT, db_index=True)

    presentations = models.ManyToManyField(Presentation, through="ProductPresentation", blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def is_orderable(self) -> bool:
        return self.status == self.Status.PUBLISHED and self.category.is_active


class ProductPresentation(models.Model):
    """Allowed presentation for a product; price adjustment only when staff set one."""

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="product_presentations")
    presentation = models.ForeignKey(Presentation, on_delete=models.PROTECT)
    price_adjustment_kes = models.IntegerField(default=0, help_text="Added to the variant price. Usually 0.")

    class Meta:
        unique_together = [("product", "presentation")]

    def __str__(self):
        return f"{self.product} / {self.presentation}"


class ProductVariant(models.Model):
    """A selectable price tier, owned by the server. Prices are whole KES."""

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="variants")
    label = models.CharField(max_length=80, help_text="Customer-facing label, e.g. 'Classic (12 stems)'")
    price_kes = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    stock_quantity = models.PositiveIntegerField(
        null=True, blank=True, help_text="Only used when the product tracks stock."
    )

    class Meta:
        ordering = ["sort_order", "price_kes"]
        unique_together = [("product", "label")]

    def __str__(self):
        return f"{self.product} - {self.label}"

    def clean(self):
        if self.product_id and self.product.stock_policy == Product.StockPolicy.TRACKED and self.stock_quantity is None:
            raise ValidationError({"stock_quantity": "Stock quantity is required for tracked products."})

    @property
    def availability(self) -> str:
        if not self.is_active or not self.product.is_orderable:
            return "unavailable"
        if self.product.stock_policy == Product.StockPolicy.MADE_TO_ORDER:
            return "made_to_order"
        return "in_stock" if (self.stock_quantity or 0) > 0 else "out_of_stock"


class DeliveryOption(models.Model):
    """A delivery area and its fee. Values are configured by staff."""

    slug = models.SlugField(max_length=80, unique=True)
    name = models.CharField(max_length=120)
    fee_kes = models.PositiveIntegerField()
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self):
        return f"{self.name} (KES {self.fee_kes})"


class DeliverySlot(models.Model):
    label = models.CharField(max_length=60, help_text="e.g. '9am - 12pm'")
    start_time = models.TimeField()
    end_time = models.TimeField()
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["start_time"]

    def __str__(self):
        return self.label

    def clean(self):
        if self.start_time and self.end_time and self.end_time <= self.start_time:
            raise ValidationError({"end_time": "End time must be after start time."})


class BlockedDate(models.Model):
    """A date on which no deliveries are made (holiday, capacity, etc.)."""

    date = models.DateField(unique=True)
    reason = models.CharField(max_length=160, blank=True)

    class Meta:
        ordering = ["date"]

    def __str__(self):
        return str(self.date)
