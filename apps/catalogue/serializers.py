from rest_framework import serializers

from .models import DeliveryOption, DeliverySlot, Product, ProductPresentation, ProductVariant


class VariantSerializer(serializers.ModelSerializer):
    priceKes = serializers.IntegerField(source="price_kes", read_only=True)
    availability = serializers.CharField(read_only=True)

    class Meta:
        model = ProductVariant
        fields = ["id", "label", "priceKes", "availability"]


class PresentationSerializer(serializers.ModelSerializer):
    id = serializers.CharField(source="presentation.slug", read_only=True)
    name = serializers.CharField(source="presentation.name", read_only=True)
    priceAdjustmentKes = serializers.IntegerField(source="price_adjustment_kes", read_only=True)

    class Meta:
        model = ProductPresentation
        fields = ["id", "name", "priceAdjustmentKes"]


class ProductSerializer(serializers.ModelSerializer):
    id = serializers.CharField(source="slug", read_only=True)
    category = serializers.SlugRelatedField(slug_field="slug", read_only=True)
    categoryName = serializers.CharField(source="category.name", read_only=True)
    isSeasonal = serializers.BooleanField(source="is_seasonal", read_only=True)
    seasonNote = serializers.CharField(source="season_note", read_only=True)
    image = serializers.CharField(source="image_url", read_only=True)
    imageAlt = serializers.CharField(source="image_alt", read_only=True)
    stockPolicy = serializers.CharField(source="stock_policy", read_only=True)
    variants = serializers.SerializerMethodField()
    presentations = serializers.SerializerMethodField()
    startingPriceKes = serializers.IntegerField(read_only=True, default=None)

    class Meta:
        model = Product
        fields = [
            "id",
            "name",
            "category",
            "categoryName",
            "description",
            "ingredients",
            "palette",
            "occasions",
            "isSeasonal",
            "seasonNote",
            "image",
            "imageAlt",
            "stockPolicy",
            "variants",
            "presentations",
            "startingPriceKes",
        ]

    def get_variants(self, obj):
        rows = [v for v in obj.variants.all() if v.is_active]
        return VariantSerializer(rows, many=True).data

    def get_presentations(self, obj):
        rows = [pp for pp in obj.product_presentations.all() if pp.presentation.is_active]
        return PresentationSerializer(rows, many=True).data


class DeliveryOptionSerializer(serializers.ModelSerializer):
    id = serializers.CharField(source="slug", read_only=True)
    feeKes = serializers.IntegerField(source="fee_kes", read_only=True)

    class Meta:
        model = DeliveryOption
        fields = ["id", "name", "feeKes"]


class DeliverySlotSerializer(serializers.ModelSerializer):
    class Meta:
        model = DeliverySlot
        fields = ["id", "label", "start_time", "end_time"]
